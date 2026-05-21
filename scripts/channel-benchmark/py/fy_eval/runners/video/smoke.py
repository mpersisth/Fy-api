"""Video model smoke test — submit + fetch lifecycle."""

from __future__ import annotations

import asyncio

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
        "prompt": "A cat walking slowly across a sunlit room",
    }

    async with httpx.AsyncClient(timeout=30.0) as http:
        try:
            resp = await http.post(
                f"{base_url}/v1/video/submit",
                headers=headers, json=body,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            return TestResult("smoke", False, f"submit error: {e}")

        if resp.status_code != 200:
            return TestResult("smoke", False, f"submit HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        task_id = data.get("data", {}).get("task_id") or data.get("task_id", "")
        if not task_id:
            return TestResult("smoke", False, "no task_id in submit response")

        # Poll for completion (max 5 minutes)
        for _ in range(30):
            await asyncio.sleep(10)
            try:
                fetch_resp = await http.get(
                    f"{base_url}/v1/video/fetch/{task_id}",
                    headers=headers,
                )
            except Exception:
                continue

            if fetch_resp.status_code != 200:
                continue

            fetch_data = fetch_resp.json()
            status = fetch_data.get("data", {}).get("status", "")
            if status == "SUCCESS":
                return TestResult("smoke", True, f"task completed, id={task_id}")
            if status in ("FAILED", "ERROR"):
                return TestResult("smoke", False, f"task failed: {status}")

    return TestResult("smoke", False, f"task {task_id} did not complete within 5min")
