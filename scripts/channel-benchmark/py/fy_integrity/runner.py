"""Orchestrates all enabled probes sequentially."""

from __future__ import annotations

from rich.console import Console

from .client import IntegrityClient
from .config import IntegrityConfig
from .probes import ALL_PROBES
from .probes.base import BaseProbe, ProbeResult


class IntegrityRunner:
    def __init__(self, config: IntegrityConfig, *, console: Console | None = None):
        self.config = config
        self.console = console or Console()

    async def run_all(self, *, probe_filter: str | None = None) -> list[ProbeResult]:
        results: list[ProbeResult] = []

        async with IntegrityClient(
            base_url=self.config.gateway.base_url,
            token=self.config.gateway.user_token,
            pin_channel_id=self.config.gateway.pin_channel_id,
            timeout_sec=self.config.target.request_timeout_sec,
        ) as client:
            probes = self._enabled_probes(probe_filter)
            for probe in probes:
                self.console.print(f"  [{probe.name}] running...", style="dim")
                result = await probe.run(client, self.config)
                results.append(result)
                self._print_result(result)

        return results

    def _enabled_probes(self, probe_filter: str | None) -> list[BaseProbe]:
        probes: list[BaseProbe] = []
        cfg = self.config.probes
        enabled_map = {
            "cache_integrity": cfg.cache.enabled,
            "token_inflation": cfg.inflation.enabled,
            "determinism": cfg.determinism.enabled,
            "tool_use_passthrough": cfg.tool_use.enabled,
            "stream_repackaging": cfg.stream.enabled,
            "content_filtering": cfg.filtering.enabled,
            "cross_user_cache_isolation": cfg.isolation.enabled,
        }
        for probe_cls in ALL_PROBES:
            instance = probe_cls()
            if probe_filter and instance.name != probe_filter:
                continue
            if enabled_map.get(instance.name, True):
                probes.append(instance)
        return probes

    def _print_result(self, result: ProbeResult) -> None:
        if result.passed:
            icon = "[green]PASS[/green]"
        elif result.severity == "critical":
            icon = "[red bold]FAIL[/red bold]"
        else:
            icon = "[yellow]WARN[/yellow]"
        self.console.print(f"  [{result.probe_name}] {icon} {result.summary}")
