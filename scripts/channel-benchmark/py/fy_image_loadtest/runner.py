"""Continuous multi-channel image load runner."""

from __future__ import annotations

import asyncio
import signal
import time
from collections import Counter
from dataclasses import dataclass, field

from rich.console import Console

from .client import ImageClient, ImageResult
from .config import ChannelTarget, Config


def _percentile_ms(values_s: list[float], q: float) -> float:
    if not values_s:
        return 0.0
    xs = sorted(values_s)
    if len(xs) == 1:
        return xs[0] * 1000.0
    rank = q * (len(xs) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    frac = rank - lo
    return (xs[lo] + (xs[hi] - xs[lo]) * frac) * 1000.0


@dataclass
class ChannelStats:
    name: str
    pin_channel_id: int
    started_at: float
    total: int = 0
    ok: int = 0
    failed: int = 0
    images: int = 0
    response_bytes: int = 0
    in_flight: int = 0
    has_b64_json_ok: int = 0
    has_url_ok: int = 0
    revised_prompt_hits: int = 0
    issued: int = 0
    cooldown_until: float = 0.0
    latencies_s: list[float] = field(default_factory=list)
    status_codes: Counter[int] = field(default_factory=Counter)
    error_breakdown: Counter[str] = field(default_factory=Counter)
    issue_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def record(self, result: ImageResult) -> None:
        self.total += 1
        self.status_codes[result.http_status] += 1
        self.response_bytes += result.response_bytes
        self.latencies_s.append(result.e2e_s)
        if result.success:
            self.ok += 1
            self.images += result.images
            if result.has_b64_json:
                self.has_b64_json_ok += 1
            if result.has_url:
                self.has_url_ok += 1
            self.revised_prompt_hits += result.revised_prompt_count
        else:
            self.failed += 1
            self.error_breakdown[result.error] += 1

    def elapsed_s(self, now: float | None = None) -> float:
        if now is None:
            now = time.monotonic()
        return max(now - self.started_at, 0.001)

    def success_rate_pct(self) -> float:
        return (self.ok / self.total * 100.0) if self.total else 0.0

    def requests_per_min(self, now: float | None = None) -> float:
        return self.total * 60.0 / self.elapsed_s(now)

    def images_per_min(self, now: float | None = None) -> float:
        return self.images * 60.0 / self.elapsed_s(now)

    def avg_response_kib(self) -> float:
        if self.total == 0:
            return 0.0
        return self.response_bytes / self.total / 1024.0

    def e2e_p50_ms(self) -> float:
        return _percentile_ms(self.latencies_s, 0.50)

    def e2e_p95_ms(self) -> float:
        return _percentile_ms(self.latencies_s, 0.95)

    def e2e_p99_ms(self) -> float:
        return _percentile_ms(self.latencies_s, 0.99)

    def top_error(self) -> str:
        if not self.error_breakdown:
            return ""
        sig, n = max(self.error_breakdown.items(), key=lambda kv: kv[1])
        sig = sig if len(sig) <= 90 else sig[:87] + "..."
        return f"{sig} (x{n})"


@dataclass
class SuiteResult:
    base_url: str
    model: str
    prompt: str
    size: str
    quality: str
    n: int
    concurrency_per_channel: int
    started_at_unix: float
    channels: list[ChannelStats]
    stopped_reason: str


class ImageRamp:
    def __init__(self, cfg: Config, *, console: Console | None = None):
        self.cfg = cfg
        self.console = console or Console()
        self._stop_reason = "signal"
        self._stats_snapshot: list[ChannelStats] = []

    async def run(self) -> SuiteResult:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass

        started_at = time.time()
        deadline_monotonic = (
            time.monotonic() + self.cfg.image.duration_sec
            if self.cfg.image.duration_sec is not None
            else None
        )
        stats = [
            ChannelStats(
                name=channel.name,
                pin_channel_id=channel.pin_channel_id,
                started_at=time.monotonic(),
            )
            for channel in self.cfg.gateway.channels
        ]
        self._stats_snapshot = stats
        stats_by_id = {s.pin_channel_id: s for s in stats}
        target_counts = {
            ch.pin_channel_id: self.cfg.image.max_requests_per_channel
            for ch in self.cfg.gateway.channels
        }

        reporter = asyncio.create_task(self._report_loop(stop_event, stats))
        tasks: list[asyncio.Task] = []
        try:
            for channel in self.cfg.gateway.channels:
                await self._warmup_channel(channel)
                channel_concurrency = channel.concurrency or self.cfg.image.concurrency_per_channel
                for worker_idx in range(channel_concurrency):
                    if self.cfg.image.startup_stagger_ms > 0 and worker_idx > 0:
                        await asyncio.sleep(self.cfg.image.startup_stagger_ms / 1000.0)
                    tasks.append(
                        asyncio.create_task(
                            self._worker(
                                channel=channel,
                                stats=stats_by_id[channel.pin_channel_id],
                                stop_event=stop_event,
                                max_requests=target_counts[channel.pin_channel_id],
                                deadline_monotonic=deadline_monotonic,
                            )
                        )
                    )

            if (
                not self.cfg.image.continuous
                and self.cfg.image.max_requests_per_channel is None
                and self.cfg.image.duration_sec is None
            ):
                raise ValueError(
                    "image.continuous=false requires image.max_requests_per_channel or image.duration_sec"
                )

            if deadline_monotonic is not None:
                duration_watcher = asyncio.create_task(
                    self._watch_duration(stop_event, deadline_monotonic)
                )
                tasks.append(duration_watcher)

            await asyncio.gather(*tasks)
        finally:
            stop_event.set()
            reporter.cancel()
            await asyncio.gather(reporter, return_exceptions=True)
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        return SuiteResult(
            base_url=self.cfg.gateway.base_url,
            model=self.cfg.image.model,
            prompt=self.cfg.image.prompt,
            size=self.cfg.image.size,
            quality=self.cfg.image.quality,
            n=self.cfg.image.n,
            concurrency_per_channel=self.cfg.image.concurrency_per_channel,
            started_at_unix=started_at,
            channels=stats,
            stopped_reason=self._stop_reason,
        )

    async def _warmup_channel(self, channel: ChannelTarget) -> None:
        if self.cfg.image.warmup_requests <= 0:
            return
        self.console.print(
            f"[bold cyan]warmup:[/] {channel.name} ({channel.pin_channel_id}) x{self.cfg.image.warmup_requests}"
        )
        async with ImageClient(
            self.cfg.gateway.base_url,
            self.cfg.gateway.user_token,
            request_timeout=self.cfg.image.request_timeout_sec,
            pin_channel_id=channel.pin_channel_id,
        ) as client:
            body = self._request_body()
            for _ in range(self.cfg.image.warmup_requests):
                await client.generate(body)

    async def _worker(
        self,
        *,
        channel: ChannelTarget,
        stats: ChannelStats,
        stop_event: asyncio.Event,
        max_requests: int | None,
        deadline_monotonic: float | None,
    ) -> None:
        async with ImageClient(
            self.cfg.gateway.base_url,
            self.cfg.gateway.user_token,
            request_timeout=self.cfg.image.request_timeout_sec,
            pin_channel_id=channel.pin_channel_id,
        ) as client:
            body = self._request_body()
            while not stop_event.is_set():
                if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                    self._stop_reason = "duration_sec reached"
                    stop_event.set()
                    break
                if not await self._reserve_request_slot(stats, max_requests):
                    if max_requests is not None and self._all_channels_reached_limit(max_requests):
                        self._stop_reason = "completed max_requests_per_channel"
                        stop_event.set()
                    break
                stats.in_flight += 1
                try:
                    now = time.monotonic()
                    if stats.cooldown_until > now:
                        await asyncio.sleep(stats.cooldown_until - now)
                    result = await client.generate(body)
                    stats.record(result)
                    if result.insufficient_quota:
                        self._stop_reason = "insufficient quota"
                        stop_event.set()
                        break
                    if result.retry_after_s > 0:
                        stats.cooldown_until = max(
                            stats.cooldown_until,
                            time.monotonic() + result.retry_after_s,
                        )
                finally:
                    stats.in_flight -= 1
                if (
                    max_requests is not None
                    and stats.issued >= max_requests
                    and self._all_channels_reached_limit(max_requests)
                ):
                    self._stop_reason = "completed max_requests_per_channel"
                    stop_event.set()

    def _all_channels_reached_limit(self, max_requests: int) -> bool:
        return all(
            ch.issued >= max_requests
            for ch in self._stats_snapshot
        )

    async def _reserve_request_slot(
        self,
        stats: ChannelStats,
        max_requests: int | None,
    ) -> bool:
        async with stats.issue_lock:
            if max_requests is not None and stats.issued >= max_requests:
                return False
            stats.issued += 1
            return True

    async def _watch_duration(
        self,
        stop_event: asyncio.Event,
        deadline_monotonic: float,
    ) -> None:
        remaining = deadline_monotonic - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)
        if not stop_event.is_set():
            self._stop_reason = "duration_sec reached"
            stop_event.set()

    async def _report_loop(
        self,
        stop_event: asyncio.Event,
        stats: list[ChannelStats],
    ) -> None:
        while not stop_event.is_set():
            await asyncio.sleep(self.cfg.image.report_interval_sec)
            if stop_event.is_set():
                break
            self._print_snapshot(stats)

    def _print_snapshot(self, stats: list[ChannelStats]) -> None:
        self.console.rule("[bold magenta]image snapshot")
        for s in stats:
            color = "green" if s.failed == 0 else ("yellow" if s.success_rate_pct() >= 95 else "red")
            self.console.print(
                f"[{color}]{s.name}#{s.pin_channel_id}[/] "
                f"ok={s.ok}/{s.total} "
                f"succ={s.success_rate_pct():.1f}% "
                f"in_flight={s.in_flight} "
                f"ipm={s.images_per_min():.2f} "
                f"e2e_p50={s.e2e_p50_ms():.0f}ms "
                f"e2e_p95={s.e2e_p95_ms():.0f}ms "
                f"avg_resp={s.avg_response_kib():.1f}KiB"
            )

    def _request_body(self) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self.cfg.image.model,
            "prompt": self.cfg.image.prompt,
            "size": self.cfg.image.size,
            "quality": self.cfg.image.quality,
            "n": self.cfg.image.n,
        }
        if self.cfg.image.response_format is not None:
            body["response_format"] = self.cfg.image.response_format
        if self.cfg.image.moderation is not None:
            body["moderation"] = self.cfg.image.moderation
        if self.cfg.image.background is not None:
            body["background"] = self.cfg.image.background
        if self.cfg.image.output_format is not None:
            body["output_format"] = self.cfg.image.output_format
        if self.cfg.image.output_compression is not None:
            body["output_compression"] = self.cfg.image.output_compression
        if self.cfg.image.user is not None:
            body["user"] = self.cfg.image.user
        return body
