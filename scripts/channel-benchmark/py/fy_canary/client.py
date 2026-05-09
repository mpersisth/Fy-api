"""Minimal HTTP client for canary probes. Non-streaming JSON only."""

from __future__ import annotations

import httpx


class CanaryClient:
    """A non-streaming OpenAI-compat client. Keeps full text + usage.

    We intentionally don't reuse fy_loadtest.ChatClient because its streaming
    machinery isn't needed here and carrying unused code paths invites bugs.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 120.0,
        pin_channel_id: int | None = None,
    ):
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        # Fy-api admin-only channel pin: append "-{id}" to the user token.
        # See middleware/auth.go ~line 431.
        effective_key = api_key if pin_channel_id is None else f"{api_key}-{pin_channel_id}"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=timeout, write=30, pool=10),
            headers={
                "Authorization": f"Bearer {effective_key}",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "CanaryClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def complete(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int = 200,
        temperature: float = 1.0,
        seed: int | None = None,
    ) -> str:
        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if seed is not None:
            body["seed"] = seed
        try:
            resp = await self._client.post(self._url, json=body)
        except httpx.HTTPError:
            return ""
        if resp.status_code >= 400:
            return ""
        try:
            data = resp.json()
        except ValueError:
            return ""
        choices = data.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message") or {}).get("content", "") or ""
