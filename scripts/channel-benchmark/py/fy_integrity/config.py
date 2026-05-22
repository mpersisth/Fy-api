"""YAML config parsing with ${ENV} expansion and validation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(raw: str) -> str:
    missing: list[str] = []

    def _resolve(line: str) -> str:
        def _sub(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            missing.append(name)
            return ""

        return _ENV_RE.sub(_sub, line)

    expanded_lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.lstrip(" \t")
        if stripped.startswith("#"):
            expanded_lines.append(line)
        else:
            expanded_lines.append(_resolve(line))

    if missing:
        raise ValueError(
            f"config references undefined environment variables: {sorted(set(missing))}"
        )
    return "\n".join(expanded_lines)


@dataclass
class GatewayCfg:
    base_url: str
    user_token: str
    pin_channel_id: int | None = None
    secondary_token: str | None = None


@dataclass
class TargetCfg:
    model: str
    max_tokens: int = 256
    request_timeout_sec: float = 120.0


@dataclass
class CacheProbeConfig:
    enabled: bool = True
    rounds: int = 5


@dataclass
class InflationProbeConfig:
    enabled: bool = True
    tolerance_tokens: int = 10


@dataclass
class DeterminismProbeConfig:
    enabled: bool = True
    rounds: int = 5
    min_consistency: float = 0.95


@dataclass
class ToolUseProbeConfig:
    enabled: bool = True


@dataclass
class StreamProbeConfig:
    enabled: bool = True
    rounds: int = 3
    burst_threshold: float = 0.5


@dataclass
class FilteringProbeConfig:
    enabled: bool = True


@dataclass
class IsolationProbeConfig:
    enabled: bool = False
    rounds: int = 3


@dataclass
class ProbesCfg:
    cache: CacheProbeConfig = field(default_factory=CacheProbeConfig)
    inflation: InflationProbeConfig = field(default_factory=InflationProbeConfig)
    determinism: DeterminismProbeConfig = field(default_factory=DeterminismProbeConfig)
    tool_use: ToolUseProbeConfig = field(default_factory=ToolUseProbeConfig)
    stream: StreamProbeConfig = field(default_factory=StreamProbeConfig)
    filtering: FilteringProbeConfig = field(default_factory=FilteringProbeConfig)
    isolation: IsolationProbeConfig = field(default_factory=IsolationProbeConfig)


@dataclass
class ExportCfg:
    formats: list[str] = field(default_factory=lambda: ["json", "markdown"])
    output_dir: str = "integrity-results"


@dataclass
class IntegrityConfig:
    gateway: GatewayCfg
    target: TargetCfg
    probes: ProbesCfg = field(default_factory=ProbesCfg)
    export: ExportCfg = field(default_factory=ExportCfg)

    @classmethod
    def load(cls, path: str | Path) -> IntegrityConfig:
        raw = Path(path).read_text(encoding="utf-8")
        expanded = _expand_env(raw)
        d = yaml.safe_load(expanded) or {}

        gw = d.get("gateway", {})
        gateway = GatewayCfg(
            base_url=gw["base_url"],
            user_token=gw["user_token"],
            pin_channel_id=gw.get("pin_channel_id"),
            secondary_token=gw.get("secondary_token") or None,
        )

        tgt = d.get("target", {})
        target = TargetCfg(
            model=tgt["model"],
            max_tokens=int(tgt.get("max_tokens", 256)),
            request_timeout_sec=float(tgt.get("request_timeout_sec", 120.0)),
        )

        p = d.get("probes", {})
        probes = ProbesCfg(
            cache=CacheProbeConfig(**p.get("cache", {})),
            inflation=InflationProbeConfig(**p.get("inflation", {})),
            determinism=DeterminismProbeConfig(**p.get("determinism", {})),
            tool_use=ToolUseProbeConfig(**p.get("tool_use", {})),
            stream=StreamProbeConfig(**p.get("stream", {})),
            filtering=FilteringProbeConfig(**p.get("filtering", {})),
            isolation=IsolationProbeConfig(**p.get("isolation", {})),
        )

        e = d.get("export", {})
        export = ExportCfg(
            formats=e.get("formats", ["json", "markdown"]),
            output_dir=e.get("output_dir", "integrity-results"),
        )

        cfg = cls(gateway=gateway, target=target, probes=probes, export=export)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.gateway.pin_channel_id is not None and self.gateway.pin_channel_id <= 0:
            raise ValueError("pin_channel_id must be > 0")
        if self.probes.isolation.enabled and not self.gateway.secondary_token:
            raise ValueError("isolation probe requires gateway.secondary_token")
        if self.probes.cache.rounds < 1:
            raise ValueError("cache.rounds must be >= 1")
        if self.probes.determinism.rounds < 2:
            raise ValueError("determinism.rounds must be >= 2")
        if not (0.0 < self.probes.determinism.min_consistency <= 1.0):
            raise ValueError("determinism.min_consistency must be in (0, 1]")
        if not (0.0 < self.probes.stream.burst_threshold <= 1.0):
            raise ValueError("stream.burst_threshold must be in (0, 1]")
        for fmt in self.export.formats:
            if fmt not in ("json", "markdown"):
                raise ValueError(f"unsupported export format: {fmt}")
