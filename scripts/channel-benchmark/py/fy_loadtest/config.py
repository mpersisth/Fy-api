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
class ChannelTarget:
    name: str
    pin_channel_id: int


@dataclass
class Gateway:
    base_url: str
    user_token: str
    pin_channel_id: int | None = None
    channels: list[ChannelTarget] = field(default_factory=list)


@dataclass
class AutoRamp:
    enabled: bool = False
    max_concurrency: int = 256
    stop_success_pct: float = 90.0
    stop_rps_gain_pct: float = 5.0
    start_concurrency: int = 1


@dataclass
class CeilingFinder:
    enabled: bool = False
    start_concurrency: int = 1
    max_concurrency: int = 128
    stop_429_pct: float = 10.0
    requests_per_probe: int = 30
    sustain_duration_s: float = 60.0
    sustain_max_requests: int = 300
    use_header_hints: bool = True


@dataclass
class LoadProfile:
    model: str
    models: list[str] = field(default_factory=list)
    prompt: str = "Reply with the single word: pong."
    max_tokens: int = 64
    temperature: float | None = None
    stream: bool = True

    concurrency_levels: list[int] = field(default_factory=lambda: [1, 10, 30, 50, 100])
    requests_per_level: int = 50
    warmup_requests: int = 5

    request_timeout_sec: float = 120.0
    auto_ramp: AutoRamp = field(default_factory=AutoRamp)
    ceiling_finder: CeilingFinder = field(default_factory=CeilingFinder)


@dataclass
class Slo:
    ttft_p95_ms: float | None = None
    itl_p95_ms: float | None = None
    e2e_p95_ms: float | None = None


@dataclass
class ExportConfig:
    formats: list[str] = field(default_factory=lambda: ["json", "markdown", "pdf"])
    output_dir: str = "loadtest-results"


@dataclass
class Config:
    gateway: Gateway
    load: LoadProfile
    slo: Slo = field(default_factory=Slo)
    export: ExportConfig = field(default_factory=ExportConfig)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(_expand_env(text)) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, d: dict) -> Config:
        gw = d.get("gateway") or {}
        ld = d.get("load") or {}
        slo = d.get("slo") or {}
        exp = d.get("export") or {}

        if not gw.get("base_url"):
            raise ValueError("gateway.base_url is required")
        if not gw.get("user_token"):
            raise ValueError("gateway.user_token is required (OpenAI-compatible bearer)")
        models_raw = ld.get("models") or []
        model_single = ld.get("model") or ""
        if not model_single and not models_raw:
            raise ValueError("load.model or load.models is required")

        channels: list[ChannelTarget] = []
        for ch in gw.get("channels") or []:
            channels.append(ChannelTarget(
                name=str(ch.get("name", f"channel-{ch['pin_channel_id']}")),
                pin_channel_id=int(ch["pin_channel_id"]),
            ))

        pin = gw.get("pin_channel_id")
        if pin is not None and not channels:
            channels.append(ChannelTarget(
                name=f"channel-{int(pin)}",
                pin_channel_id=int(pin),
            ))

        all_models = list(models_raw) if models_raw else ([model_single] if model_single else [])
        primary_model = all_models[0] if all_models else ""

        ar_raw = ld.get("auto_ramp") or {}
        auto_ramp = AutoRamp(
            enabled=bool(ar_raw.get("enabled", False)),
            max_concurrency=int(ar_raw.get("max_concurrency", 256)),
            stop_success_pct=float(ar_raw.get("stop_success_pct", 90.0)),
            stop_rps_gain_pct=float(ar_raw.get("stop_rps_gain_pct", 5.0)),
            start_concurrency=int(ar_raw.get("start_concurrency", 1)),
        )

        cf_raw = ld.get("ceiling_finder") or {}
        ceiling_finder = CeilingFinder(
            enabled=bool(cf_raw.get("enabled", False)),
            start_concurrency=int(cf_raw.get("start_concurrency", 1)),
            max_concurrency=int(cf_raw.get("max_concurrency", 128)),
            stop_429_pct=float(cf_raw.get("stop_429_pct", 10.0)),
            requests_per_probe=int(cf_raw.get("requests_per_probe", 30)),
            sustain_duration_s=float(cf_raw.get("sustain_duration_s", 60.0)),
            sustain_max_requests=int(cf_raw.get("sustain_max_requests", 300)),
            use_header_hints=bool(cf_raw.get("use_header_hints", True)),
        )

        return cls(
            gateway=Gateway(
                base_url=gw["base_url"],
                user_token=gw["user_token"],
                pin_channel_id=int(pin) if pin is not None else None,
                channels=channels,
            ),
            load=LoadProfile(
                model=primary_model,
                models=all_models,
                prompt=ld.get("prompt", LoadProfile.prompt),
                max_tokens=int(ld.get("max_tokens", 64)),
                temperature=float(ld["temperature"]) if "temperature" in ld else None,
                stream=bool(ld.get("stream", True)),
                concurrency_levels=list(ld.get("concurrency_levels", [1, 10, 30, 50, 100])),
                requests_per_level=int(ld.get("requests_per_level", 50)),
                warmup_requests=int(ld.get("warmup_requests", 5)),
                request_timeout_sec=float(ld.get("request_timeout_sec", 120.0)),
                auto_ramp=auto_ramp,
                ceiling_finder=ceiling_finder,
            ),
            slo=Slo(
                ttft_p95_ms=slo.get("ttft_p95_ms"),
                itl_p95_ms=slo.get("itl_p95_ms"),
                e2e_p95_ms=slo.get("e2e_p95_ms"),
            ),
            export=ExportConfig(
                formats=list(exp.get("formats", ["json", "markdown", "pdf"])),
                output_dir=str(exp.get("output_dir", "loadtest-results")),
            ),
        )

    _VALID_FORMATS = {"json", "csv", "markdown", "pdf"}

    def validate(self) -> None:
        import warnings
        if self.load.ceiling_finder.enabled and self.load.auto_ramp.enabled:
            warnings.warn("both ceiling_finder and auto_ramp are enabled; ceiling_finder takes priority, auto_ramp will be ignored")
        if not self.load.auto_ramp.enabled and not self.load.ceiling_finder.enabled and not self.load.concurrency_levels:
            raise ValueError("load.concurrency_levels must have at least one entry (or enable auto_ramp/ceiling_finder)")
        if any(c <= 0 for c in self.load.concurrency_levels):
            raise ValueError(f"load.concurrency_levels must be positive: {self.load.concurrency_levels}")
        if not self.load.models and self.load.model:
            self.load.models = [self.load.model]
        if not self.load.models:
            raise ValueError("load.model or load.models must specify at least one model")
        if self.load.requests_per_level <= 0:
            raise ValueError("load.requests_per_level must be > 0")
        if self.load.warmup_requests < 0:
            raise ValueError("load.warmup_requests must be >= 0")
        if self.gateway.pin_channel_id is not None and self.gateway.pin_channel_id <= 0:
            raise ValueError(
                f"gateway.pin_channel_id must be > 0, got {self.gateway.pin_channel_id}"
            )
        for ch in self.gateway.channels:
            if ch.pin_channel_id <= 0:
                raise ValueError(
                    f"channel {ch.name!r}: pin_channel_id must be > 0, got {ch.pin_channel_id}"
                )
        bad = set(self.export.formats) - self._VALID_FORMATS
        if bad:
            raise ValueError(f"unknown export formats: {sorted(bad)} (valid: {sorted(self._VALID_FORMATS)})")
        if not self.export.formats:
            raise ValueError("export.formats must have at least one entry")
