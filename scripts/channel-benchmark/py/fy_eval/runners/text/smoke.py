"""Text model smoke test — basic chat completion connectivity."""

from __future__ import annotations

import httpx

from ...config import Config
from ...orchestrator import TestResult


async def run(cfg: Config, model: str) -> TestResult:
    base_url = cfg.channel.base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {cfg.channel.user_token}",
        "Content-Type": "application/json",
    }
    if cfg.channel.pin_channel_id:
        headers["X-Oneapi-Channel"] = str(cfg.channel.pin_channel_id)

    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Say hello in one word."}],
        "max_tokens": 10,
    }

    async with httpx.AsyncClient(timeout=30.0) as http:
        try:
            resp = await http.post(
                f"{base_url}/v1/chat/completions",
                headers=headers,
                json=body,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            return TestResult("smoke", False, f"connection error: {e}")

        if resp.status_code != 200:
            return TestResult("smoke", False, f"HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return TestResult("smoke", False, "no choices in response")

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            return TestResult("smoke", False, "empty content in response")

    return TestResult("smoke", True, f"OK ({len(content)} chars)")
