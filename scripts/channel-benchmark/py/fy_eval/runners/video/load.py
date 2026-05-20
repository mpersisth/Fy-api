"""Video model load test — concurrent task submissions."""

from __future__ import annotations

import asyncio
import time

import httpx

from ...config import Config
from ...orchestrator import TestResult


async def run(cfg: Config, model: str) -> TestResult:
    load_cfg = cfg.video_models.tests.load if cfg.video_models else {}
    concurrency = load_cfg.get("concurrency", 1) if isinstance(load_cfg, dict) else 1
    max_requests = load_cfg.get("max_requests", 3) if isinstance(load_cfg, dict) else 3

    base_url = cfg.channel.base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {cfg.channel.user_token}",
        "Content-Type": "application/json",
    }
    if cfg.channel.pin_channel_id:
        headers["X-Oneapi-Channel"] = str(cfg.channel.pin_channel_id)

    results: list[tuple[bool, float]] = []
    sem = asyncio.Semaphore(concurrency)

    async def submit_one():
        async with sem:
            body = {"model": model, "prompt": "A bird flying over the ocean"}
            t0 = time.perf_counter()
            async with httpx.AsyncClient(timeout=30.0) as http:
                try:
                    resp = await http.post(
                        f"{base_url}/v1/video/submit",
                        headers=headers, json=body,
                    )
                    elapsed = time.perf_counter() - t0
                    results.append((resp.status_code == 200, elapsed))
                except Exception:
                    results.append((False, time.perf_counter() - t0))

    tasks = [asyncio.create_task(submit_one()) for _ in range(max_requests)]
    await asyncio.gather(*tasks)

    successes = sum(1 for ok, _ in results if ok)
    total = len(results)
    rate = successes / total if total else 0
    latencies = sorted(e for ok, e in results if ok)
    avg = sum(latencies) / len(latencies) if latencies else 0

    metrics = {"total": total, "successes": successes, "success_rate": rate, "avg_sec": avg}

    if rate < 0.8:
        return TestResult("load", False, f"success rate {rate:.0%}", metrics)
    return TestResult("load", True, f"success={rate:.0%}, avg={avg:.1f}s, n={total}", metrics)
