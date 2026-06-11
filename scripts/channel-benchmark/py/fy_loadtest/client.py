"""Async OpenAI-compatible chat client with real SSE parsing.

Mirrors the Go smoke tool's measurement semantics so apples-to-apples
comparison between smoke and load-test runs is meaningful:
  - TTFT is measured from the first CONTENT chunk (role-only preamble chunks
    are intentionally excluded — that is the reason we did not reuse
    genai-perf, whose TTFT anchors on the first SSE chunk regardless of
    content).
  - ITL samples are gaps between consecutive content chunks; TTFT is NOT in
    the ITL list (llmperf's well-known bug — we do not repeat it).
  - Usage, when present in the final pre-[DONE] chunk, is authoritative.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import httpx

_DONE_MARK = "[DONE]"


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class ChatResult:
    """All measurements from one request. Time units: seconds (monotonic).

    A field being 0 means "not measured" — either because the mode doesn't
    apply (TTFT on non-stream) or the upstream didn't report it (usage).
    """

    # Outcome
    success: bool = False
    http_status: int = 0
    error: str = ""

    # Timing
    e2e_s: float = 0.0
    ttft_s: float = 0.0                       # stream only; 0 if not streamed
    inter_token_gaps_s: list[float] = field(default_factory=list)  # stream only

    # Content / counting
    content_chars: int = 0
    content_text: str = ""
    chunks: int = 0
    finish_reason: str = ""
    usage: Usage = field(default_factory=Usage)

    # Metadata
    streamed: bool = False
    started_at: float = 0.0              # time.time() wall-clock when dispatched
    rate_limit_headers: dict[str, str] = field(default_factory=dict)

    def tokens_per_sec(self) -> float:
        """Decode throughput. Excludes prefill/queue (TTFT) for stream runs."""
        if self.usage.completion_tokens <= 0:
            return 0.0
        if self.streamed and self.ttft_s > 0:
            decode = self.e2e_s - self.ttft_s
        else:
            decode = self.e2e_s
        if decode <= 0:
            return 0.0
        return self.usage.completion_tokens / decode

    def tpot_s(self) -> float:
        """Time per output token. Industry standard: (E2E - TTFT) / (N-1).

        Returns 0 when we can't meaningfully compute it (non-stream, or <2
        completion tokens, or missing TTFT).
        """
        if not self.streamed or self.usage.completion_tokens < 2 or self.ttft_s <= 0:
            return 0.0
        decode = self.e2e_s - self.ttft_s
        if decode <= 0:
            return 0.0
        return decode / (self.usage.completion_tokens - 1)


class ChatClient:
    """Thin wrapper over httpx.AsyncClient for OpenAI-compatible /v1/chat/completions.

    Holds one AsyncClient so connections pool across requests, which matters
    under concurrency — re-handshaking per request would skew TTFT upward.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        request_timeout: float = 120.0,
        pin_channel_id: int | None = None,
        extra_headers: dict[str, str] | None = None,
    ):
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        # Channel-pin is implemented by appending "-{id}" to the user token.
        # Fy-api admin-only feature; see middleware/auth.go ~line 431.
        effective_token = token if pin_channel_id is None else f"{token}-{pin_channel_id}"
        headers: dict[str, str] = {
            "Authorization": f"Bearer {effective_token}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        # Generous connect timeout, tight read timeout controlled by caller.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=request_timeout,
                write=30.0,
                pool=10.0,
            ),
            headers=headers,
            http2=False,  # Fy-api advertises HTTP/1.1 for SSE; avoid h2/SSE quirks
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> ChatClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def chat(
        self,
        *,
        model: str,
        prompt: str = "",
        max_tokens: int,
        temperature: float | None,
        stream: bool,
        messages: list[dict] | None = None,
    ) -> ChatResult:
        if messages is not None:
            body: dict[str, object] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "stream": stream,
            }
        else:
            body = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "stream": stream,
            }
        if temperature is not None:
            body["temperature"] = temperature
        if stream:
            # Ask Fy-api to include usage in the final pre-[DONE] chunk. Some
            # upstreams drop usage from streams without this flag.
            body["stream_options"] = {"include_usage": True}

        result = ChatResult(streamed=stream)
        result.started_at = time.time()
        t0 = time.monotonic()
        try:
            if stream:
                await self._do_stream(body, result, t0)
            else:
                await self._do_json(body, result, t0)
        except httpx.TimeoutException as e:
            result.e2e_s = time.monotonic() - t0
            result.error = f"timeout: {e}"
        except httpx.HTTPError as e:
            result.e2e_s = time.monotonic() - t0
            result.error = f"http: {e}"
        except Exception as e:  # safety net — never let a task die mid-run
            result.e2e_s = time.monotonic() - t0
            result.error = f"unexpected: {e!r}"
        return result

    async def _do_json(self, body: dict, result: ChatResult, t0: float) -> None:
        resp = await self._client.post(self._url, json=body)
        result.http_status = resp.status_code
        result.rate_limit_headers = _capture_rl_headers(resp)
        result.e2e_s = time.monotonic() - t0
        if resp.status_code >= 400:
            result.error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            return
        payload = resp.json()
        choices = payload.get("choices") or []
        if choices:
            msg = (choices[0].get("message") or {}).get("content", "")
            result.content_chars = len(msg)
            result.content_text = msg
            result.finish_reason = choices[0].get("finish_reason", "") or ""
        _extract_usage(payload.get("usage"), result.usage)
        result.success = True

    async def _do_stream(self, body: dict, result: ChatResult, t0: float) -> None:
        async with self._client.stream("POST", self._url, json=body) as resp:
            result.http_status = resp.status_code
            result.rate_limit_headers = _capture_rl_headers(resp)
            if resp.status_code >= 400:
                # Drain to get server error text, but cap it so a megabyte error
                # page doesn't blow memory.
                err_text = ""
                async for chunk in resp.aiter_text():
                    err_text += chunk
                    if len(err_text) > 2048:
                        break
                result.e2e_s = time.monotonic() - t0
                result.error = f"HTTP {resp.status_code}: {err_text[:500]}"
                return

            last_content_at = 0.0
            content_parts: list[str] = []
            async for raw_line in resp.aiter_lines():
                if not raw_line:
                    continue
                if not raw_line.startswith("data:"):
                    continue
                payload = raw_line[5:].strip()
                if payload == _DONE_MARK:
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    # Vendor-specific metadata chunks happen; skip gracefully.
                    continue

                _extract_usage(chunk.get("usage"), result.usage)

                for ch in chunk.get("choices") or []:
                    delta = ch.get("delta") or {}
                    content = delta.get("content") or ""
                    if content:
                        now = time.monotonic()
                        if result.ttft_s == 0.0:
                            result.ttft_s = now - t0
                        elif last_content_at > 0:
                            result.inter_token_gaps_s.append(now - last_content_at)
                        last_content_at = now
                        result.chunks += 1
                        result.content_chars += len(content)
                        content_parts.append(content)
                    fr = ch.get("finish_reason")
                    if fr and not result.finish_reason:
                        result.finish_reason = str(fr)

        result.e2e_s = time.monotonic() - t0
        result.content_text = "".join(content_parts)
        # Successful iff we got some content OR usage (some providers legitimately
        # emit empty content for safety filters but still report tokens).
        if result.content_chars > 0 or result.usage.completion_tokens > 0:
            result.success = True
        elif not result.error:
            result.error = "stream closed with no content and no usage"


_RL_PREFIXES = ("x-ratelimit-", "retry-after", "ratelimit-")


def _capture_rl_headers(resp: httpx.Response) -> dict[str, str]:
    return {
        k.lower(): v
        for k, v in resp.headers.items()
        if any(k.lower().startswith(p) for p in _RL_PREFIXES)
    }


def _extract_usage(raw: dict | None, into: Usage) -> None:
    if not raw:
        return
    into.prompt_tokens = int(raw.get("prompt_tokens", into.prompt_tokens) or 0)
    into.completion_tokens = int(raw.get("completion_tokens", into.completion_tokens) or 0)
    into.total_tokens = int(raw.get("total_tokens", into.total_tokens) or 0)
    details = raw.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is not None:
        into.cached_tokens = int(cached)
