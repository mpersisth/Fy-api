"""Concurrency-ramp driver.

For each concurrency level C in config.load.concurrency_levels:
  1. Optional warmup: fire N warmup requests at the same concurrency, discard.
  2. Main run: keep exactly C requests in flight until
     requests_per_level completions have been collected.
  3. Collect per-request results, aggregate with metrics.aggregate_level.

Why closed-loop constant concurrency (not Poisson arrivals):
  - Simpler. The load we produce equals the load the gateway is actually
    asked to hold. Users of the report can read "at concurrency 25, p95
    TTFT was X" without having to reason about arrival-rate mathematics.
  - Matches llmperf / genai-perf --concurrency semantics so cross-comparing
    is meaningful.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from rich.console import Console

from .client import ChatClient, ChatResult
from .config import Config
from .metrics import LevelAggregate, aggregate_level


@dataclass
class RampResult:
    levels: list[LevelAggregate]
    model: str
    base_url: str


class Ramp:
    def __init__(self, cfg: Config, *, console: Console | None = None):
        self.cfg = cfg
        self.console = console or Console()

    async def run(self) -> RampResult:
        aggregates: list[LevelAggregate] = []
        async with ChatClient(
            self.cfg.gateway.base_url,
            self.cfg.gateway.user_token,
            request_timeout=self.cfg.load.request_timeout_sec,
        ) as client:
            for concurrency in self.cfg.load.concurrency_levels:
                self.console.rule(f"[bold cyan]concurrency={concurrency}")
                if self.cfg.load.warmup_requests > 0:
                    self.console.print(
                        f"  warmup: {self.cfg.load.warmup_requests} requests at C={concurrency}"
                    )
                    await self._fire(
                        client,
                        concurrency=concurrency,
                        total=self.cfg.load.warmup_requests,
                        is_warmup=True,
                    )

                self.console.print(
                    f"  measure: {self.cfg.load.requests_per_level} requests at C={concurrency}"
                )
                t_start = time.monotonic()
                results = await self._fire(
                    client,
                    concurrency=concurrency,
                    total=self.cfg.load.requests_per_level,
                    is_warmup=False,
                )
                wall = time.monotonic() - t_start

                agg = aggregate_level(
                    concurrency=concurrency,
                    results=results,
                    wall_time_s=wall,
                    slo=self.cfg.slo,
                )
                aggregates.append(agg)
                self._print_level_summary(agg)

        return RampResult(
            levels=aggregates,
            model=self.cfg.load.model,
            base_url=self.cfg.gateway.base_url,
        )

    async def _fire(
        self,
        client: ChatClient,
        *,
        concurrency: int,
        total: int,
        is_warmup: bool,
    ) -> list[ChatResult]:
        """Keep exactly `concurrency` requests in flight until `total` complete.

        We use a semaphore-bounded worker pool: spawn `total` tasks but block
        each on acquiring a semaphore permit. This is a closed-loop model
        rather than a fixed worker pool; the difference doesn't matter for
        our metrics, and this shape is simpler to reason about and cancel.
        """
        sem = asyncio.Semaphore(concurrency)
        results: list[ChatResult] = []
        results_lock = asyncio.Lock()

        async def one():
            async with sem:
                r = await client.chat(
                    model=self.cfg.load.model,
                    prompt=self.cfg.load.prompt,
                    max_tokens=self.cfg.load.max_tokens,
                    temperature=self.cfg.load.temperature,
                    stream=self.cfg.load.stream,
                )
                if not is_warmup:
                    async with results_lock:
                        results.append(r)

        tasks = [asyncio.create_task(one()) for _ in range(total)]
        await asyncio.gather(*tasks, return_exceptions=False)
        return results

    def _print_level_summary(self, a: LevelAggregate) -> None:
        parts = [
            f"ok={a.ok}/{a.total}",
            f"succ={a.success_rate_pct:.1f}%",
            f"e2e_p50={a.e2e.p50_ms:.0f}ms",
            f"e2e_p95={a.e2e.p95_ms:.0f}ms",
        ]
        if a.ttft.samples:
            parts.append(f"ttft_p50={a.ttft.p50_ms:.0f}ms")
            parts.append(f"ttft_p95={a.ttft.p95_ms:.0f}ms")
        parts.append(f"rps={a.throughput_req_per_s:.2f}")
        parts.append(f"tok/s={a.aggregate_tok_per_s:.1f}")
        if a.goodput_req_per_s is not None:
            parts.append(f"goodput={a.goodput_req_per_s:.2f}")
        color = "green" if a.failed == 0 else ("yellow" if a.success_rate_pct >= 95 else "red")
        self.console.print(f"  [{color}]result: " + "  ".join(parts) + "[/]")
