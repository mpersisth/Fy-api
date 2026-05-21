"""Text model load test — concurrent chat completions."""

from __future__ import annotations

import asyncio
import time

import httpx

from ...config import Config
from ...orchestrator import TestResult


async def run(cfg: Config, model: str) -> TestResult:
    load_cfg = cfg.text_models.tests.load if cfg.text_models else {}
    concurrency = load_cfg.get("concurrency", 5) if isinstance(load_cfg, dict) else 5
    duration = load_cfg.get("duration_sec", 60) if isinstance(load_cfg, dict) else 60

    base_url = cfg.channel.base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {cfg.channel.user_token}",
        "Content-Type": "application/json",
    }
    if cfg.channel.pin_channel_id:
        headers["X-Oneapi-Channel"] = str(cfg.channel.pin_channel_id)

    successes = 0
    failures = 0
    latencies: list[float] = []
    stop = asyncio.Event()

    async def worker():
        nonlocal successes, failures
        async with httpx.AsyncClient(timeout=60.0) as http:
            while not stop.is_set():
                body = {
                    "model": model,
                    "messages": [{"role": "user", "content": "Count from 1 to 5."}],
                    "max_tokens": 50,
                }
                t0 = time.perf_counter()
                try:
                    resp = await http.post(
                        f"{base_url}/v1/chat/completions",
                        headers=headers, json=body,
                    )
                    elapsed = time.perf_counter() - t0
                    if resp.status_code == 200:
                        successes += 1
                        latencies.append(elapsed)
                    else:
                        failures += 1
                except Exception:
                    failures += 1

    tasks = [asyncio.create_task(worker()) for _ in range(concurrency)]
    await asyncio.sleep(duration)
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)

    total = successes + failures
    if total == 0:
        return TestResult("load", False, "no requests completed")

    rate = successes / total
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0

    metrics = {
        "total": total,
        "successes": successes,
        "failures": failures,
        "success_rate": rate,
        "p95_sec": p95,
        "avg_sec": sum(latencies) / len(latencies) if latencies else 0,
        "concurrency": concurrency,
        "duration_sec": duration,
    }

    if rate < 0.8:
        return TestResult("load", False, f"success rate {rate:.0%} < 80%", metrics)
    detail = f"success={rate:.0%}, P95={p95:.1f}s, n={total}"
    return TestResult("load", True, detail, metrics)
