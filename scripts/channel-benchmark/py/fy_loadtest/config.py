"""YAML config parsing with ${ENV} expansion and validation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Matches ${VAR} and ${VAR:-default}. Mirrors Go-side regex so the two
# harnesses parse the same config the same way.
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(raw: str) -> str:
    """Replace ${VAR} / ${VAR:-default} in raw YAML text.

    Comment lines (first non-whitespace char '#') are skipped so that example
    snippets in docs don't trigger 'missing env var' errors.
    """
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
class Gateway:
    base_url: str
    user_token: str  # sk-... — billed to the user, mirrors Go tool

    # When set, every request appends "-{pin_channel_id}" to user_token, which
    # Fy-api parses (middleware/auth.go ~line 431) as a forced channel
    # selection. user_token must belong to an admin — otherwise the gateway
    # returns 403 "普通用户不支持指定渠道".
    #
    # None = go through the normal distributor (group + priority + weight +
    # affinity). Recommended ON when you're load-testing one specific channel,
    # because a model offered by N channels would otherwise be load-balanced
    # across them and the throughput numbers wouldn't say anything about any
    # single channel.
    pin_channel_id: int | None = None


@dataclass
class LoadProfile:
    """What and how much to send."""
    model: str
    prompt: str = "Reply with the single word: pong."
    max_tokens: int = 64
    temperature: float = 0.0
    stream: bool = True

    # Request volume + concurrency.
    concurrency_levels: list[int] = field(default_factory=lambda: [1, 2, 5, 10, 25, 50, 100])
    requests_per_level: int = 50
    warmup_requests: int = 5

    # Per-request ceiling. Lower than the load test should ever need; when we
    # trip this, we WANT the test to record a timeout rather than hang.
    request_timeout_sec: float = 120.0


@dataclass
class Slo:
    """Optional latency SLOs. A request is 'good' iff every SLO is met.
    Goodput = good_requests / wall_clock.  None = goodput not reported.
    """
    ttft_p95_ms: float | None = None
    itl_p95_ms: float | None = None
    e2e_p95_ms: float | None = None


@dataclass
class ExportConfig:
    formats: list[str] = field(default_factory=lambda: ["json", "markdown"])
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
        if not ld.get("model"):
            raise ValueError("load.model is required")

        return cls(
            gateway=Gateway(
                base_url=gw["base_url"],
                user_token=gw["user_token"],
                pin_channel_id=(
                    int(gw["pin_channel_id"]) if gw.get("pin_channel_id") is not None else None
                ),
            ),
            load=LoadProfile(
                model=ld["model"],
                prompt=ld.get("prompt", LoadProfile.prompt),
                max_tokens=int(ld.get("max_tokens", 64)),
                temperature=float(ld.get("temperature", 0.0)),
                stream=bool(ld.get("stream", True)),
                concurrency_levels=list(ld.get("concurrency_levels", [1, 2, 5, 10, 25, 50, 100])),
                requests_per_level=int(ld.get("requests_per_level", 50)),
                warmup_requests=int(ld.get("warmup_requests", 5)),
                request_timeout_sec=float(ld.get("request_timeout_sec", 120.0)),
            ),
            slo=Slo(
                ttft_p95_ms=slo.get("ttft_p95_ms"),
                itl_p95_ms=slo.get("itl_p95_ms"),
                e2e_p95_ms=slo.get("e2e_p95_ms"),
            ),
            export=ExportConfig(
                formats=list(exp.get("formats", ["json", "markdown"])),
                output_dir=str(exp.get("output_dir", "loadtest-results")),
            ),
        )

    def validate(self) -> None:
        if not self.load.concurrency_levels:
            raise ValueError("load.concurrency_levels must have at least one entry")
        if any(c <= 0 for c in self.load.concurrency_levels):
            raise ValueError(f"load.concurrency_levels must be positive: {self.load.concurrency_levels}")
        if self.load.requests_per_level <= 0:
            raise ValueError("load.requests_per_level must be > 0")
        if self.load.warmup_requests < 0:
            raise ValueError("load.warmup_requests must be >= 0")
        if self.gateway.pin_channel_id is not None and self.gateway.pin_channel_id <= 0:
            raise ValueError(
                f"gateway.pin_channel_id must be > 0, got {self.gateway.pin_channel_id}"
            )
