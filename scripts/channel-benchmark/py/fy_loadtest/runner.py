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
from .metrics import (
    CeilingResult,
    LevelAggregate,
    aggregate_level,
    classify_limit_type,
    extract_header_limits,
)


@dataclass
class RampResult:
    levels: list[LevelAggregate]
    model: str
    base_url: str
    pin_channel_id: int | None = None
    channel_name: str = ""
    bottleneck_concurrency: int | None = None
    auto_ramped: bool = False
    ceiling: CeilingResult | None = None


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
            cf = self.cfg.load.ceiling_finder
            ar = self.cfg.load.auto_ramp
            if cf.enabled:
                return await self._run_ceiling_finder(
                    client, model=model,
                    pin_channel_id=pin_channel_id, channel_name=channel_name,
                )
            elif ar.enabled:
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
            agg, _ = await self._measure_level(client, model=model, concurrency=concurrency)
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
            agg, _ = await self._measure_level(client, model=model, concurrency=c)
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
        total_requests: int | None = None,
    ) -> tuple[LevelAggregate, list[ChatResult]]:
        total = total_requests or self.cfg.load.requests_per_level
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
            f"  measure: {total} requests at C={concurrency}"
        )
        t_start = time.monotonic()
        results = await self._fire(
            client, model=model, concurrency=concurrency,
            total=total, is_warmup=False,
        )
        wall = time.monotonic() - t_start

        agg = aggregate_level(
            concurrency=concurrency, results=results,
            wall_time_s=wall, slo=self.cfg.slo,
        )
        self._print_level_summary(agg)
        return agg, results

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

    async def _run_ceiling_finder(
        self,
        client: ChatClient,
        *,
        model: str,
        pin_channel_id: int | None,
        channel_name: str,
    ) -> RampResult:
        cf = self.cfg.load.ceiling_finder
        aggregates: list[LevelAggregate] = []
        all_raw: list[ChatResult] = []
        c = cf.start_concurrency
        ceiling_c: int | None = None
        first_429_c: int | None = None
        header_rpm: float | None = None
        header_tpm: float | None = None

        self.console.print(
            f"[bold cyan]ceiling-finder:[/] start={c}, max={cf.max_concurrency}, "
            f"stop_429>{cf.stop_429_pct}%, sustain={cf.sustain_duration_s}s"
        )

        # Phase 1: coarse ramp
        prev_rpm = 0.0
        while c <= cf.max_concurrency:
            agg, raw = await self._measure_level(
                client, model=model, concurrency=c,
                total_requests=cf.requests_per_probe,
            )
            aggregates.append(agg)
            all_raw.extend(raw)

            if cf.use_header_hints and header_rpm is None and header_tpm is None:
                header_rpm, header_tpm = extract_header_limits(raw)
                if header_rpm or header_tpm:
                    self.console.print(
                        f"  [green]header hint:[/] rpm_limit={header_rpm}, tpm_limit={header_tpm}"
                    )
                    ceiling_c = c
                    break

            if agg.errors_429 > 0 and first_429_c is None:
                first_429_c = c

            if agg.error_rate_429_pct >= cf.stop_429_pct:
                self.console.print(
                    f"  [red]ceiling stop:[/] 429 rate {agg.error_rate_429_pct:.1f}% "
                    f">= {cf.stop_429_pct}% at C={c}"
                )
                ceiling_c = aggregates[-2].concurrency if len(aggregates) >= 2 else c
                break

            if prev_rpm > 0 and agg.rpm > 0:
                rpm_gain = (agg.rpm - prev_rpm) / prev_rpm
                if rpm_gain < 0.03:
                    self.console.print(
                        f"  [yellow]ceiling stop:[/] RPM plateau (gain={rpm_gain*100:.1f}%) at C={c}"
                    )
                    ceiling_c = c
                    break

            prev_rpm = agg.rpm
            c *= 2

        if ceiling_c is None and aggregates:
            ceiling_c = aggregates[-1].concurrency

        # Phase 2: sustained measurement
        self.console.rule(f"[bold green]稳态验证: C={ceiling_c}, duration={cf.sustain_duration_s}s")
        sustain_agg, sustain_raw = await self._measure_sustained(
            client, model=model, concurrency=ceiling_c or 1,
            duration_s=cf.sustain_duration_s,
            max_requests=cf.sustain_max_requests,
        )
        aggregates.append(sustain_agg)
        all_raw.extend(sustain_raw)

        if header_rpm is None and header_tpm is None:
            header_rpm, header_tpm = extract_header_limits(sustain_raw)

        has_429 = any(a.errors_429 > 0 for a in aggregates)
        confidence = "high" if (header_rpm or header_tpm) else ("medium" if has_429 else "low")

        ceiling = CeilingResult(
            measured_rpm=sustain_agg.rpm,
            measured_input_tpm=sustain_agg.input_tpm,
            measured_output_tpm=sustain_agg.output_tpm,
            measured_total_tpm=sustain_agg.total_tpm,
            header_rpm_limit=header_rpm,
            header_tpm_limit=header_tpm,
            limit_type=classify_limit_type(header_rpm, header_tpm),
            confidence=confidence,
            ceiling_concurrency=ceiling_c or 1,
            first_429_concurrency=first_429_c,
            sustain_success_rate_pct=sustain_agg.success_rate_pct,
            sustain_duration_s=sustain_agg.wall_time_s,
        )

        self.console.print(
            f"  [bold green]ceiling result:[/] RPM={ceiling.measured_rpm:.0f} "
            f"TPM={ceiling.measured_total_tpm:.0f} "
            f"type={ceiling.limit_type} confidence={ceiling.confidence}"
        )

        return RampResult(
            levels=aggregates, model=model,
            base_url=self.cfg.gateway.base_url,
            pin_channel_id=pin_channel_id, channel_name=channel_name,
            bottleneck_concurrency=ceiling_c, auto_ramped=True,
            ceiling=ceiling,
        )

    async def _measure_sustained(
        self,
        client: ChatClient,
        *,
        model: str,
        concurrency: int,
        duration_s: float,
        max_requests: int,
    ) -> tuple[LevelAggregate, list[ChatResult]]:
        """Run at fixed concurrency for a time window to get steady-state metrics."""
        sem = asyncio.Semaphore(concurrency)
        results: list[ChatResult] = []
        results_lock = asyncio.Lock()
        stop = asyncio.Event()
        count = 0
        count_lock = asyncio.Lock()

        async def worker():
            nonlocal count
            while not stop.is_set():
                async with count_lock:
                    if count >= max_requests:
                        return
                    count += 1
                async with sem:
                    if stop.is_set():
                        return
                    r = await client.chat(
                        model=model,
                        prompt=self.cfg.load.prompt,
                        max_tokens=self.cfg.load.max_tokens,
                        temperature=self.cfg.load.temperature,
                        stream=self.cfg.load.stream,
                    )
                    async with results_lock:
                        results.append(r)

        t0 = time.monotonic()
        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await asyncio.sleep(duration_s)
        stop.set()
        await asyncio.gather(*workers, return_exceptions=True)
        wall = time.monotonic() - t0

        self.console.print(
            f"  sustain done: {len(results)} requests in {wall:.1f}s"
        )

        agg = aggregate_level(
            concurrency=concurrency, results=results,
            wall_time_s=wall, slo=self.cfg.slo,
        )
        self._print_level_summary(agg)
        return agg, results

    def _print_level_summary(self, a: LevelAggregate) -> None:
        parts = [
            f"ok={a.ok}/{a.total}",
            f"succ={a.success_rate_pct:.1f}%",
            f"rpm={a.rpm:.1f}",
            f"e2e_p50={a.e2e.p50_ms:.0f}ms",
            f"e2e_p95={a.e2e.p95_ms:.0f}ms",
        ]
        if a.ttft.samples:
            parts.append(f"ttft_p50={a.ttft.p50_ms:.0f}ms")
            parts.append(f"ttft_p95={a.ttft.p95_ms:.0f}ms")
        if a.tpot.samples:
            parts.append(f"tpot_p50={a.tpot.p50_ms:.0f}ms")
        parts.append(f"rps={a.throughput_req_per_s:.2f}")
        parts.append(f"tok/s={a.aggregate_tok_per_s:.1f}")
        parts.append(f"in_tpm={a.input_tpm:.0f}")
        parts.append(f"out_tpm={a.output_tpm:.0f}")
        if a.errors_429 or a.errors_5xx or a.errors_timeout:
            err_parts = []
            if a.errors_429:
                err_parts.append(f"429={a.errors_429}({a.error_rate_429_pct:.1f}%)")
            if a.errors_5xx:
                err_parts.append(f"5xx={a.errors_5xx}({a.error_rate_5xx_pct:.1f}%)")
            if a.errors_timeout:
                err_parts.append(f"timeout={a.errors_timeout}({a.error_rate_timeout_pct:.1f}%)")
            parts.append(" ".join(err_parts))
        if a.goodput_req_per_s is not None:
            parts.append(f"goodput={a.goodput_req_per_s:.2f}")
        color = "green" if a.failed == 0 else ("yellow" if a.success_rate_pct >= 95 else "red")
        self.console.print(f"  [{color}]result: " + "  ".join(parts) + "[/]")
