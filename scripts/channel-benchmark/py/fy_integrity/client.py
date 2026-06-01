"""HTTP client for integrity probes.

Returns full response metadata (usage, tool_calls, raw JSON) needed
by the various probes to detect middleman manipulation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class CompletionResult:
    success: bool
    http_status: int = 0
    content: str = ""
    usage: dict = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)
    raw_response: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class StreamChunk:
    content: str
    timestamp: float


@dataclass
class StreamResult:
    success: bool
    http_status: int = 0
    chunks: list[StreamChunk] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    total_content: str = ""
    error: str = ""


class IntegrityClient:
    """httpx.AsyncClient wrapper for integrity probes."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        pin_channel_id: int | None = None,
        timeout_sec: float = 120.0,
    ):
        effective_token = (
            f"{token}-{pin_channel_id}" if pin_channel_id else token
        )
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {effective_token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(
                connect=10.0, read=timeout_sec, write=30.0, pool=10.0
            ),
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int = 256,
        temperature: float | None = None,
        tools: list[dict] | None = None,
    ) -> CompletionResult:
        body: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = tools
        try:
            resp = await self._client.post(self._url, json=body)
            if resp.status_code != 200:
                return CompletionResult(
                    success=False,
                    http_status=resp.status_code,
                    error=resp.text[:500],
                )
            data = resp.json()
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            return CompletionResult(
                success=True,
                http_status=200,
                content=msg.get("content", "") or "",
                usage=data.get("usage", {}),
                tool_calls=msg.get("tool_calls", []),
                raw_response=data,
            )
        except Exception as e:
            return CompletionResult(success=False, error=str(e))

    async def stream_with_timing(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int = 256,
    ) -> StreamResult:
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        chunks: list[StreamChunk] = []
        usage: dict = {}
        try:
            async with self._client.stream(
                "POST", self._url, json=body
            ) as resp:
                if resp.status_code != 200:
                    body_text = await resp.aread()
                    return StreamResult(
                        success=False,
                        http_status=resp.status_code,
                        error=body_text.decode()[:500],
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("usage"):
                        usage = obj["usage"]
                    delta = (
                        obj.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content")
                    )
                    if delta:
                        chunks.append(
                            StreamChunk(
                                content=delta, timestamp=time.monotonic()
                            )
                        )
            return StreamResult(
                success=True,
                http_status=200,
                chunks=chunks,
                usage=usage,
                total_content="".join(c.content for c in chunks),
            )
        except Exception as e:
            return StreamResult(success=False, error=str(e))
