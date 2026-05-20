"""Layer 4: Performance — load testing for image generation channels."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from collections import Counter

from ..client import ImageClient
from ..config import Config, ChannelTarget


@dataclass
class PerfStats:
    channel: ChannelTarget
    total_requests: int = 0
    successes: int = 0
    failures: int = 0
    latencies_sec: list[float] = field(default_factory=list)
    errors: Counter = field(default_factory=Counter)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.total_requests if self.total_requests else 0.0

    @property
    def p50_ms(self) -> float:
        return _percentile(self.latencies_sec, 0.5) * 1000

    @property
    def p95_ms(self) -> float:
        return _percentile(self.latencies_sec, 0.95) * 1000

    @property
    def p99_ms(self) -> float:
        return _percentile(self.latencies_sec, 0.99) * 1000

    @property
    def avg_ms(self) -> float:
        if not self.latencies_sec:
            return 0.0
        return (sum(self.latencies_sec) / len(self.latencies_sec)) * 1000

    @property
    def rpm(self) -> float:
        elapsed = self.end_time - self.start_time
        if elapsed <= 0:
            return 0.0
        return self.total_requests / elapsed * 60


async def run(cfg: Config, client: ImageClient) -> list[PerfStats]:
    perf_cfg = cfg.suites.perf
    if not perf_cfg.enabled:
        return []

    results = []
    for ch in cfg.gateway.channels:
        concurrency = ch.concurrency or perf_cfg.concurrency_per_channel
        stats = PerfStats(channel=ch, start_time=time.perf_counter())

        stop_event = asyncio.Event()
        sem = asyncio.Semaphore(concurrency)

        async def worker():
            while not stop_event.is_set():
                async with sem:
                    if stop_event.is_set():
                        break
                    body = {
                        "model": cfg.model.name,
                        "prompt": cfg.model.default_prompt,
                        "n": 1,
                    }
                    r = await client.generate(body, pin_channel=ch.pin_channel_id)
                    stats.total_requests += 1
                    if r.success:
                        stats.successes += 1
                        stats.latencies_sec.append(r.elapsed_sec)
                    else:
                        stats.failures += 1
                        err_key = f"{r.status_code}" if r.status_code else "timeout"
                        stats.errors[err_key] += 1

                    if perf_cfg.max_requests_per_channel:
                        if stats.total_requests >= perf_cfg.max_requests_per_channel:
                            stop_event.set()

        tasks = [asyncio.create_task(worker()) for _ in range(concurrency)]

        if perf_cfg.duration_sec:
            await asyncio.sleep(perf_cfg.duration_sec)
            stop_event.set()

        await asyncio.gather(*tasks, return_exceptions=True)
        stats.end_time = time.perf_counter()
        results.append(stats)

    return results


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    rank = q * (len(xs) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    frac = rank - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac
