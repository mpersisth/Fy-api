"""Image model smoke test — basic generation + output validation + latency."""

from __future__ import annotations

import base64
import time

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
        "prompt": "a red apple on a white wooden table, studio lighting",
        "n": 1,
    }

    async with httpx.AsyncClient(timeout=300.0) as http:
        t0 = time.perf_counter()
        try:
            resp = await http.post(
                f"{base_url}/v1/images/generations",
                headers=headers, json=body,
            )
            generation_sec = time.perf_counter() - t0
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            return TestResult("smoke", False, f"connection error: {e}")

        if resp.status_code != 200:
            return TestResult("smoke", False, f"HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        items = data.get("data", [])
        if not items:
            return TestResult("smoke", False, "no data in response")

        item = items[0]
        if item.get("url"):
            try:
                img_resp = await http.get(item["url"], timeout=30.0)
                img_resp.raise_for_status()
                img_bytes = img_resp.content
            except Exception as e:
                return TestResult("smoke", False, f"image URL not accessible: {e}")
        elif item.get("b64_json"):
            try:
                img_bytes = base64.b64decode(item["b64_json"])
            except Exception:
                return TestResult("smoke", False, "invalid base64 data")
        else:
            return TestResult("smoke", False, "no url or b64_json in response")

    if len(img_bytes) < 1000:
        return TestResult("smoke", False, f"image too small ({len(img_bytes)} bytes)")

    metrics = {"generation_sec": round(generation_sec, 2), "size_bytes": len(img_bytes)}
    detail = f"OK, {generation_sec:.1f}s, {len(img_bytes)//1024}KB"
    return TestResult("smoke", True, detail, metrics)
