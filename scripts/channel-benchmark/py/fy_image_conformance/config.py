"""Unified YAML config for image conformance testing."""

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

    expanded: list[str] = []
    for line in raw.splitlines():
        if line.lstrip().startswith("#"):
            expanded.append(line)
        else:
            expanded.append(_resolve(line))
    if missing:
        raise ValueError(f"undefined env vars: {sorted(set(missing))}")
    return "\n".join(expanded)


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
class ModelProfile:
    name: str
    supported_sizes: list[str] = field(default_factory=lambda: ["1024x1024"])
    supported_qualities: list[str] = field(default_factory=lambda: ["standard"])
    supported_formats: list[str] = field(default_factory=lambda: ["png"])
    supports_n_gt_1: bool = False
    max_n: int = 1
    supports_background: bool = False
    supports_moderation: bool = False
    default_prompt: str = "a red apple on a white wooden table, studio lighting"


@dataclass
class PerfConfig:
    enabled: bool = True
    concurrency_per_channel: int = 2
    duration_sec: float = 120.0
    request_timeout_sec: float = 300.0
    warmup_requests: int = 1
    max_requests_per_channel: int | None = None
    startup_stagger_ms: int = 200


@dataclass
class PromptFollowConfig:
    enabled: bool = False
    judge_model: str = "gpt-4o"
    judge_base_url: str | None = None
    judge_token: str | None = None
    sample_count: int = 3
    judge_repeat: int = 3
    consistency_threshold: float = 0.1


@dataclass
class SuiteFlags:
    api_compat: bool = True
    output_valid: bool = True
    prompt_follow: PromptFollowConfig = field(default_factory=PromptFollowConfig)
    perf: PerfConfig = field(default_factory=PerfConfig)
    safety: bool = True


@dataclass
class ExportConfig:
    output_dir: str = "image-conformance-results"


@dataclass
class BudgetConfig:
    max_cost_usd: float | None = None
    warn_cost_usd: float | None = None
    cost_model: dict[str, float] = field(default_factory=dict)
    default_cost_per_request: float = 0.04


@dataclass
class Config:
    gateway: Gateway
    model: ModelProfile
    suites: SuiteFlags = field(default_factory=SuiteFlags)
    export: ExportConfig = field(default_factory=ExportConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(_expand_env(text)) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, d: dict) -> Config:
        gw = d.get("gateway") or {}
        mdl = d.get("model") or {}
        st = d.get("suites") or {}
        exp = d.get("export") or {}
        bdg = d.get("budget") or {}

        if not gw.get("base_url"):
            raise ValueError("gateway.base_url is required")
        if not gw.get("user_token"):
            raise ValueError("gateway.user_token is required")
        if not mdl.get("name"):
            raise ValueError("model.name is required")

        channels = [
            ChannelTarget(
                name=str(ch.get("name", f"ch-{ch['pin_channel_id']}")),
                pin_channel_id=int(ch["pin_channel_id"]),
                concurrency=int(ch["concurrency"]) if ch.get("concurrency") else None,
            )
            for ch in (gw.get("channels") or [])
        ]
        if not channels:
            raise ValueError("gateway.channels must have at least one entry")

        pf = st.get("prompt_follow") or {}
        perf = st.get("perf") or {}

        return cls(
            gateway=Gateway(
                base_url=str(gw["base_url"]),
                user_token=str(gw["user_token"]),
                channels=channels,
            ),
            model=ModelProfile(
                name=str(mdl["name"]),
                supported_sizes=mdl.get("supported_sizes", ["1024x1024"]),
                supported_qualities=mdl.get("supported_qualities", ["standard"]),
                supported_formats=mdl.get("supported_formats", ["png"]),
                supports_n_gt_1=bool(mdl.get("supports_n_gt_1", False)),
                max_n=int(mdl.get("max_n", 1)),
                supports_background=bool(mdl.get("supports_background", False)),
                supports_moderation=bool(mdl.get("supports_moderation", False)),
                default_prompt=str(mdl.get("default_prompt",
                    "a red apple on a white wooden table, studio lighting")),
            ),
            suites=SuiteFlags(
                api_compat=bool(st.get("api_compat", True)),
                output_valid=bool(st.get("output_valid", True)),
                prompt_follow=PromptFollowConfig(
                    enabled=bool(pf.get("enabled", False)),
                    judge_model=str(pf.get("judge_model", "gpt-4o")),
                    judge_base_url=pf.get("judge_base_url"),
                    judge_token=pf.get("judge_token"),
                    sample_count=int(pf.get("sample_count", 3)),
                    judge_repeat=int(pf.get("judge_repeat", 3)),
                    consistency_threshold=float(pf.get("consistency_threshold", 0.1)),
                ),
                perf=PerfConfig(
                    enabled=bool(perf.get("enabled", True)),
                    concurrency_per_channel=int(perf.get("concurrency_per_channel", 2)),
                    duration_sec=float(perf.get("duration_sec", 120.0)),
                    request_timeout_sec=float(perf.get("request_timeout_sec", 300.0)),
                    warmup_requests=int(perf.get("warmup_requests", 1)),
                    max_requests_per_channel=perf.get("max_requests_per_channel"),
                    startup_stagger_ms=int(perf.get("startup_stagger_ms", 200)),
                ),
                safety=bool(st.get("safety", True)),
            ),
            export=ExportConfig(
                output_dir=str(exp.get("output_dir", "image-conformance-results")),
            ),
            budget=BudgetConfig(
                max_cost_usd=float(bdg["max_cost_usd"]) if bdg.get("max_cost_usd") is not None else None,
                warn_cost_usd=float(bdg["warn_cost_usd"]) if bdg.get("warn_cost_usd") is not None else None,
                cost_model=bdg.get("cost_model") or {},
                default_cost_per_request=float(bdg.get("default_cost_per_request", 0.04)),
            ),
        )
