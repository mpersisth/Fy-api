"""Image model load test — concurrent generation with parameter matrix."""

from __future__ import annotations

import asyncio
import itertools
import time

import httpx

from ...config import Config
from ...orchestrator import TestResult


def _expand_matrix(raw) -> list[dict]:
    """Expand a dimension dict into cartesian product of all combinations."""
    if not raw:
        return [{}]
    if isinstance(raw, list):
        return raw
    # raw is a dict like {size: [...], quality: [...]}
    keys = list(raw.keys())
    values = [v if isinstance(v, list) else [v] for v in raw.values()]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


async def run(cfg: Config, model: str) -> TestResult:
    load_cfg = cfg.image_models.tests.load if cfg.image_models else {}
    concurrency = load_cfg.get("concurrency", 2) if isinstance(load_cfg, dict) else 2
    duration = load_cfg.get("duration_sec", 60) if isinstance(load_cfg, dict) else 60
    matrix_raw = load_cfg.get("matrix") if isinstance(load_cfg, dict) else None

    base_url = cfg.channel.base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {cfg.channel.user_token}",
        "Content-Type": "application/json",
    }
    if cfg.channel.pin_channel_id:
        headers["X-Oneapi-Channel"] = str(cfg.channel.pin_channel_id)

    combinations = _expand_matrix(matrix_raw)

    all_groups: list[dict] = []
    worst_rate = 1.0

    for params in combinations:
        stats = await _run_group(base_url, headers, model, params, concurrency, duration)
        all_groups.append(stats)
        if stats["success_rate"] < worst_rate:
            worst_rate = stats["success_rate"]

    metrics = {"matrix": all_groups, "concurrency": concurrency, "duration_sec": duration}

    if worst_rate < 0.8:
        return TestResult("load", False, f"worst success rate {worst_rate:.0%} < 80%", metrics)

    summary_parts = []
    for g in all_groups:
        label = g.get("label", "default")
        summary_parts.append(f"{label}: P95={g['p95_sec']:.1f}s")
    detail = "; ".join(summary_parts)
    return TestResult("load", True, detail, metrics)


async def _run_group(
    base_url: str, headers: dict, model: str,
    params: dict, concurrency: int, duration: float,
) -> dict:
    size = params.get("size")
    quality = params.get("quality")
    label_parts = []
    if size:
        label_parts.append(size)
    if quality:
        label_parts.append(quality)
    label = " / ".join(label_parts) if label_parts else "default"

    successes = 0
    failures = 0
    latencies: list[float] = []
    stop = asyncio.Event()

    async def worker():
        nonlocal successes, failures
        async with httpx.AsyncClient(timeout=300.0) as http:
            while not stop.is_set():
                body: dict = {"model": model, "prompt": "a geometric pattern, minimal", "n": 1}
                if size:
                    body["size"] = size
                if quality:
                    body["quality"] = quality
                t0 = time.perf_counter()
                try:
                    resp = await http.post(
                        f"{base_url}/v1/images/generations",
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
    latencies.sort()
    p50 = latencies[int(len(latencies) * 0.5)] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    avg = sum(latencies) / len(latencies) if latencies else 0

    return {
        "label": label,
        "params": params,
        "total": total,
        "successes": successes,
        "success_rate": successes / total if total else 0,
        "p50_sec": round(p50, 2),
        "p95_sec": round(p95, 2),
        "avg_sec": round(avg, 2),
    }
