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
from dataclasses import dataclass, field

from rich.console import Console

from .client import ChatClient, ChatResult
from .config import ChannelTarget, Config
from .metrics import LevelAggregate, aggregate_level


@dataclass
class RampResult:
    levels: list[LevelAggregate]
    model: str
    base_url: str
    pin_channel_id: int | None = None
    channel_name: str = ""
    bottleneck_concurrency: int | None = None
    auto_ramped: bool = False


@dataclass
class MultiChannelResult:
    results: list[RampResult]
    model: str
    base_url: str


@dataclass
class SuiteResult:
    model_results: list[MultiChannelResult]
    base_url: str


class Ramp:
    def __init__(self, cfg: Config, *, console: Console | None = None):
        self.cfg = cfg
        self.console = console or Console()

    async def run_suite(self) -> SuiteResult:
        models = self.cfg.load.models
        all_mc: list[MultiChannelResult] = []
        for mi, model in enumerate(models):
            if len(models) > 1:
                self.console.rule(
                    f"[bold blue]模型 {mi+1}/{len(models)}: {model}",
                    style="bold blue",
                )
            mc = await self._run_model(model)
            all_mc.append(mc)
        return SuiteResult(model_results=all_mc, base_url=self.cfg.gateway.base_url)

    async def run(self) -> MultiChannelResult:
        return await self._run_model(self.cfg.load.model)

    async def _run_model(self, model: str) -> MultiChannelResult:
        channels = self.cfg.gateway.channels
        if not channels:
            result = await self._run_single(
                model=model, pin_channel_id=None, channel_name=""
            )
            return MultiChannelResult(
                results=[result], model=model, base_url=self.cfg.gateway.base_url,
            )

        all_results: list[RampResult] = []
        for i, ch in enumerate(channels):
            self.console.rule(
                f"[bold magenta]渠道 {i+1}/{len(channels)}: {ch.name} (id={ch.pin_channel_id})"
            )
            result = await self._run_single(
                model=model,
                pin_channel_id=ch.pin_channel_id,
                channel_name=ch.name,
            )
            all_results.append(result)

        return MultiChannelResult(
            results=all_results, model=model, base_url=self.cfg.gateway.base_url,
        )

    async def _run_single(
        self,
        *,
        model: str,
        pin_channel_id: int | None,
        channel_name: str,
    ) -> RampResult:
        if pin_channel_id is not None:
            self.console.print(
                f"[bold yellow]channel pin:[/] forcing channel id={pin_channel_id} via admin token suffix"
            )
        else:
            self.console.print(
                "[dim]channel pin:[/] none (requests go through Fy-api distributor)"
            )

        async with ChatClient(
            self.cfg.gateway.base_url,
            self.cfg.gateway.user_token,
            request_timeout=self.cfg.load.request_timeout_sec,
            pin_channel_id=pin_channel_id,
        ) as client:
            ar = self.cfg.load.auto_ramp
            if ar.enabled:
                return await self._run_auto_ramp(
                    client, model=model,
                    pin_channel_id=pin_channel_id, channel_name=channel_name,
                )
            else:
                return await self._run_fixed_levels(
                    client, model=model,
                    pin_channel_id=pin_channel_id, channel_name=channel_name,
                )

    async def _run_fixed_levels(
        self,
        client: ChatClient,
        *,
        model: str,
        pin_channel_id: int | None,
        channel_name: str,
    ) -> RampResult:
        aggregates: list[LevelAggregate] = []
        for concurrency in self.cfg.load.concurrency_levels:
            agg = await self._measure_level(client, model=model, concurrency=concurrency)
            aggregates.append(agg)
        return RampResult(
            levels=aggregates, model=model,
            base_url=self.cfg.gateway.base_url,
            pin_channel_id=pin_channel_id, channel_name=channel_name,
        )

    async def _run_auto_ramp(
        self,
        client: ChatClient,
        *,
        model: str,
        pin_channel_id: int | None,
        channel_name: str,
    ) -> RampResult:
        ar = self.cfg.load.auto_ramp
        aggregates: list[LevelAggregate] = []
        c = ar.start_concurrency
        prev_rps = 0.0
        bottleneck_c: int | None = None

        self.console.print(
            f"[bold cyan]auto-ramp:[/] start={c}, max={ar.max_concurrency}, "
            f"stop_success<{ar.stop_success_pct}%, stop_rps_gain<{ar.stop_rps_gain_pct}%"
        )

        while c <= ar.max_concurrency:
            agg = await self._measure_level(client, model=model, concurrency=c)
            aggregates.append(agg)

            rps = agg.throughput_req_per_s
            rps_gain = ((rps - prev_rps) / prev_rps * 100) if prev_rps > 0 else 100.0

            if agg.success_rate_pct < ar.stop_success_pct:
                self.console.print(
                    f"  [red]auto-ramp stop:[/] success rate {agg.success_rate_pct:.1f}% "
                    f"< {ar.stop_success_pct}% threshold at C={c}"
                )
                bottleneck_c = aggregates[-2].concurrency if len(aggregates) >= 2 else c
                break

            if prev_rps > 0 and rps_gain < ar.stop_rps_gain_pct:
                self.console.print(
                    f"  [yellow]auto-ramp stop:[/] RPS gain {rps_gain:.1f}% "
                    f"< {ar.stop_rps_gain_pct}% threshold at C={c}"
                )
                bottleneck_c = c
                break

            prev_rps = rps
            c = c * 2

        if bottleneck_c is None and aggregates:
            bottleneck_c = aggregates[-1].concurrency

        return RampResult(
            levels=aggregates, model=model,
            base_url=self.cfg.gateway.base_url,
            pin_channel_id=pin_channel_id, channel_name=channel_name,
            bottleneck_concurrency=bottleneck_c, auto_ramped=True,
        )

    async def _measure_level(
        self, client: ChatClient, *, model: str, concurrency: int,
    ) -> LevelAggregate:
        self.console.rule(f"[bold cyan]concurrency={concurrency}")
        if self.cfg.load.warmup_requests > 0:
            self.console.print(
                f"  warmup: {self.cfg.load.warmup_requests} requests at C={concurrency}"
            )
            await self._fire(
                client, model=model, concurrency=concurrency,
                total=self.cfg.load.warmup_requests, is_warmup=True,
            )

        self.console.print(
            f"  measure: {self.cfg.load.requests_per_level} requests at C={concurrency}"
        )
        t_start = time.monotonic()
        results = await self._fire(
            client, model=model, concurrency=concurrency,
            total=self.cfg.load.requests_per_level, is_warmup=False,
        )
        wall = time.monotonic() - t_start

        agg = aggregate_level(
            concurrency=concurrency, results=results,
            wall_time_s=wall, slo=self.cfg.slo,
        )
        self._print_level_summary(agg)
        return agg

    async def _fire(
        self,
        client: ChatClient,
        *,
        model: str,
        concurrency: int,
        total: int,
        is_warmup: bool,
    ) -> list[ChatResult]:
        sem = asyncio.Semaphore(concurrency)
        results: list[ChatResult] = []
        results_lock = asyncio.Lock()

        async def one():
            async with sem:
                r = await client.chat(
                    model=model,
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
