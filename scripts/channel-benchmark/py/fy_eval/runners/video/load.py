"""Video model load test — concurrent submissions with parameter matrix."""

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
    keys = list(raw.keys())
    values = [v if isinstance(v, list) else [v] for v in raw.values()]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


async def run(cfg: Config, model: str) -> TestResult:
    load_cfg = cfg.video_models.tests.load if cfg.video_models else {}
    concurrency = load_cfg.get("concurrency", 1) if isinstance(load_cfg, dict) else 1
    max_requests = load_cfg.get("max_requests", 3) if isinstance(load_cfg, dict) else 3
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
        stats = await _run_group(base_url, headers, model, params, concurrency, max_requests)
        all_groups.append(stats)
        if stats["success_rate"] < worst_rate:
            worst_rate = stats["success_rate"]

    metrics = {"matrix": all_groups, "concurrency": concurrency}

    if worst_rate < 0.8:
        return TestResult("load", False, f"worst success rate {worst_rate:.0%}", metrics)

    summary_parts = []
    for g in all_groups:
        label = g.get("label", "default")
        summary_parts.append(f"{label}: avg={g['avg_sec']:.1f}s")
    detail = "; ".join(summary_parts)
    return TestResult("load", True, detail, metrics)


async def _run_group(
    base_url: str, headers: dict, model: str,
    params: dict, concurrency: int, max_requests: int,
) -> dict:
    duration = params.get("duration")
    resolution = params.get("resolution")
    label_parts = []
    if duration:
        label_parts.append(duration)
    if resolution:
        label_parts.append(resolution)
    label = " / ".join(label_parts) if label_parts else "default"

    results: list[tuple[bool, float]] = []
    sem = asyncio.Semaphore(concurrency)

    async def submit_one():
        async with sem:
            body: dict = {"model": model, "prompt": "A bird flying over the ocean"}
            if duration:
                body["duration"] = duration
            if resolution:
                body["resolution"] = resolution
            for k, v in params.items():
                if k not in ("duration", "resolution"):
                    body[k] = v
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
    latencies = sorted(e for ok, e in results if ok)
    avg = sum(latencies) / len(latencies) if latencies else 0

    return {
        "label": label,
        "params": params,
        "total": total,
        "successes": successes,
        "success_rate": successes / total if total else 0,
        "avg_sec": round(avg, 2),
    }
