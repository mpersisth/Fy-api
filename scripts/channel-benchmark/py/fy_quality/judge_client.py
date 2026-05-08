"""Thin judge client — a ChatClient specialized for LLM-as-judge calls.

We keep judges completely separate from the channels under test so you
never accidentally have a channel judge its own output.  The judge model,
base URL, and API key are all configured independently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx


@dataclass
class JudgeVerdict:
    raw: str            # the full judge output text
    score: int | None   # parsed 1-5 or None if unparseable
    tokens_used: int    # prompt + completion tokens reported by server
    model: str
    label: str


_SCORE_RE = re.compile(r"^\s*SCORE:\s*([1-5])\s*$", re.MULTILINE)


class JudgeClient:
    """A single LLM-as-judge backend. Holds one persistent AsyncClient.

    We call judges non-streaming — we want the full verdict text to regex
    against the score line, and the judge response is short enough that
    TTFT doesn't matter.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        label: str,
        timeout: float = 60.0,
        temperature: float = 0.0,
    ):
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self.model = model
        self.label = label
        self._temperature = temperature
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=timeout, write=30, pool=10),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "JudgeClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def judge(self, prompt: str) -> JudgeVerdict:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 200,
            "temperature": self._temperature,
            "stream": False,
        }
        try:
            resp = await self._client.post(self._url, json=body)
        except httpx.HTTPError as e:
            return JudgeVerdict(raw=f"<http error: {e}>", score=None, tokens_used=0, model=self.model, label=self.label)

        if resp.status_code >= 400:
            return JudgeVerdict(
                raw=f"<HTTP {resp.status_code}: {resp.text[:200]}>",
                score=None,
                tokens_used=0,
                model=self.model,
                label=self.label,
            )

        data = resp.json()
        text = ""
        choices = data.get("choices") or []
        if choices:
            text = (choices[0].get("message") or {}).get("content", "") or ""
        usage = data.get("usage") or {}
        tokens = int(usage.get("total_tokens") or 0)

        m = _SCORE_RE.search(text)
        score = int(m.group(1)) if m else None
        return JudgeVerdict(raw=text, score=score, tokens_used=tokens, model=self.model, label=self.label)
