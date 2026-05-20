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
    concurrency: int | None = None


@dataclass
class Gateway:
    base_url: str
    user_token: str
    channels: list[ChannelTarget] = field(default_factory=list)


@dataclass
class ImageProfile:
    model: str
    prompt: str
    size: str = "1024x1024"
    quality: str = "low"
    n: int = 1
    response_format: str | None = None
    moderation: str | None = None
    background: str | None = None
    output_format: str | None = None
    output_compression: int | None = None
    user: str | None = None
    concurrency_per_channel: int = 2
    request_timeout_sec: float = 300.0
    report_interval_sec: float = 60.0
    warmup_requests: int = 0
    continuous: bool = True
    duration_sec: float | None = None
    max_requests_per_channel: int | None = None
    startup_stagger_ms: int = 0


@dataclass
class ExportConfig:
    formats: list[str] = field(default_factory=lambda: ["json", "csv", "markdown"])
    output_dir: str = "image-loadtest-results"


@dataclass
class Config:
    gateway: Gateway
    image: ImageProfile
    export: ExportConfig = field(default_factory=ExportConfig)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(_expand_env(text)) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, d: dict) -> Config:
        gw = d.get("gateway") or {}
        img = d.get("image") or {}
        exp = d.get("export") or {}

        if not gw.get("base_url"):
            raise ValueError("gateway.base_url is required")
        if not gw.get("user_token"):
            raise ValueError("gateway.user_token is required (OpenAI-compatible bearer)")
        channels_raw = gw.get("channels") or []
        if not channels_raw:
            raise ValueError("gateway.channels is required")
        channels = [
            ChannelTarget(
                name=str(ch.get("name", f"channel-{int(ch['pin_channel_id'])}")),
                pin_channel_id=int(ch["pin_channel_id"]),
                concurrency=(
                    int(ch["concurrency"])
                    if ch.get("concurrency") is not None
                    else None
                ),
            )
            for ch in channels_raw
        ]

        if not img.get("model"):
            raise ValueError("image.model is required")
        if not img.get("prompt"):
            raise ValueError("image.prompt is required")

        return cls(
            gateway=Gateway(
                base_url=str(gw["base_url"]),
                user_token=str(gw["user_token"]),
                channels=channels,
            ),
            image=ImageProfile(
                model=str(img["model"]),
                prompt=str(img["prompt"]),
                size=str(img.get("size", "1024x1024")),
                quality=str(img.get("quality", "low")),
                n=int(img.get("n", 1)),
                response_format=(
                    str(img["response_format"])
                    if img.get("response_format") is not None
                    else None
                ),
                moderation=img.get("moderation"),
                background=img.get("background"),
                output_format=img.get("output_format"),
                output_compression=img.get("output_compression"),
                user=img.get("user"),
                concurrency_per_channel=int(img.get("concurrency_per_channel", 2)),
                request_timeout_sec=float(img.get("request_timeout_sec", 300.0)),
                report_interval_sec=float(img.get("report_interval_sec", 60.0)),
                warmup_requests=int(img.get("warmup_requests", 0)),
                continuous=bool(img.get("continuous", True)),
                duration_sec=(
                    float(img["duration_sec"])
                    if img.get("duration_sec") is not None
                    else None
                ),
                max_requests_per_channel=(
                    int(img["max_requests_per_channel"])
                    if img.get("max_requests_per_channel") is not None
                    else None
                ),
                startup_stagger_ms=int(img.get("startup_stagger_ms", 0)),
            ),
            export=ExportConfig(
                formats=list(exp.get("formats", ["json", "csv", "markdown"])),
                output_dir=str(exp.get("output_dir", "image-loadtest-results")),
            ),
        )

    _VALID_FORMATS = {"json", "csv", "markdown"}

    def validate(self) -> None:
        if not self.gateway.channels:
            raise ValueError("gateway.channels must have at least one entry")
        for ch in self.gateway.channels:
            if ch.pin_channel_id <= 0:
                raise ValueError(
                    f"channel {ch.name!r}: pin_channel_id must be > 0, got {ch.pin_channel_id}"
                )
            if ch.concurrency is not None and ch.concurrency <= 0:
                raise ValueError(
                    f"channel {ch.name!r}: concurrency must be > 0, got {ch.concurrency}"
                )
        if self.image.concurrency_per_channel <= 0:
            raise ValueError("image.concurrency_per_channel must be > 0")
        if self.image.request_timeout_sec <= 0:
            raise ValueError("image.request_timeout_sec must be > 0")
        if self.image.report_interval_sec <= 0:
            raise ValueError("image.report_interval_sec must be > 0")
        if self.image.warmup_requests < 0:
            raise ValueError("image.warmup_requests must be >= 0")
        if self.image.duration_sec is not None and self.image.duration_sec <= 0:
            raise ValueError("image.duration_sec must be > 0 when set")
        if self.image.n <= 0:
            raise ValueError("image.n must be > 0")
        if (
            self.image.max_requests_per_channel is not None
            and self.image.max_requests_per_channel <= 0
        ):
            raise ValueError("image.max_requests_per_channel must be > 0 when set")
        bad = set(self.export.formats) - self._VALID_FORMATS
        if bad:
            raise ValueError(
                f"unknown export formats: {sorted(bad)} (valid: {sorted(self._VALID_FORMATS)})"
            )
