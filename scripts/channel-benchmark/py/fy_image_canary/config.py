"""YAML configuration for image canary detection."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

_VENDOR_HOSTS = {
    "api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com",
    "api.deepseek.com", "api.moonshot.cn", "dashscope.aliyuncs.com",
}


def _expand_env(raw: str) -> str:
    missing: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        missing.append(name)
        return ""

    lines = []
    for line in raw.splitlines():
        if line.lstrip().startswith("#"):
            lines.append(line)
        else:
            lines.append(_ENV_RE.sub(_sub, line))
    if missing:
        raise ValueError(f"undefined env vars: {sorted(set(missing))}")
    return "\n".join(lines)


@dataclass
class GatewayTarget:
    name: str
    base_url: str
    user_token: str
    pin_channel_id: int
    model: str


@dataclass
class VendorDirect:
    base_url: str
    api_key: str
    model: str


@dataclass
class JudgeConfig:
    model: str = "gpt-4o"
    base_url: str | None = None
    token: str | None = None
    repeat: int = 3
    consistency_threshold: float = 0.1


@dataclass
class FingerprintConfig:
    db_path: str = ""
    speed_tolerance: float = 0.5


@dataclass
class ThresholdConfig:
    clip_cosine_min: float = 0.90
    color_correlation_min: float = 0.85
    success_rate_diff_max: float = 0.10
    latency_ratio_max: float = 2.0


@dataclass
class BudgetConfig:
    max_cost_usd: float | None = None
    warn_cost_usd: float | None = None
    cost_per_generation: float = 0.04
    cost_per_judge_call: float = 0.005


DEFAULT_TEST_PROMPTS = [
    "a red apple on white table, studio lighting",
    "a blue car on a street, sunny day",
    "a mountain landscape at sunset",
    "a geometric pattern, circles and squares",
    "一个红色的灯笼挂在门前",
    "an abstract painting in blue and gold",
    "a close-up photo of a cat's face",
    "a white empty room with a single window",
]


@dataclass
class ImageCanaryConfig:
    gateway: GatewayTarget
    additional_channels: list[GatewayTarget] = field(default_factory=list)
    vendor: VendorDirect | None = None
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    fingerprint: FingerprintConfig = field(default_factory=FingerprintConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    test_prompts: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_PROMPTS))
    output_dir: str = "image-canary-results"
    request_timeout_sec: float = 300.0
    concurrency: int = 2

    @classmethod
    def load(cls, path: str | Path) -> ImageCanaryConfig:
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(_expand_env(text)) or {}
        return cls._from_dict(data, path)

    @classmethod
    def _from_dict(cls, d: dict, config_path: str | Path = "") -> ImageCanaryConfig:
        gw = d.get("gateway") or {}
        if not gw.get("base_url"):
            raise ValueError("gateway.base_url is required")
        if not gw.get("user_token"):
            raise ValueError("gateway.user_token is required")

        gateway = GatewayTarget(
            name=str(gw.get("name", "default")),
            base_url=str(gw["base_url"]),
            user_token=str(gw["user_token"]),
            pin_channel_id=int(gw.get("pin_channel_id", 0)),
            model=str(gw.get("model", "")),
        )

        additional = []
        for ac in d.get("additional_channels") or []:
            additional.append(GatewayTarget(
                name=str(ac.get("name", f"ch-{ac.get('pin_channel_id', 0)}")),
                base_url=str(ac.get("base_url", gateway.base_url)),
                user_token=str(ac.get("user_token", gateway.user_token)),
                pin_channel_id=int(ac.get("pin_channel_id", 0)),
                model=str(ac.get("model", gateway.model)),
            ))

        vd = d.get("vendor")
        vendor = None
        if vd and vd.get("base_url") and vd.get("api_key"):
            vendor = VendorDirect(
                base_url=str(vd["base_url"]),
                api_key=str(vd["api_key"]),
                model=str(vd.get("model", gateway.model)),
            )

        jd = d.get("judge") or {}
        fp_d = d.get("fingerprint") or {}
        th_d = d.get("thresholds") or {}
        bd = d.get("budget") or {}

        fp_db_path = str(fp_d.get("db_path", ""))
        if not fp_db_path and config_path:
            candidate = Path(config_path).parent / "fy_image_canary" / "fingerprints.yaml"
            if candidate.exists():
                fp_db_path = str(candidate)

        cfg = cls(
            gateway=gateway,
            additional_channels=additional,
            vendor=vendor,
            judge=JudgeConfig(
                model=str(jd.get("model", "gpt-4o")),
                base_url=jd.get("base_url"),
                token=jd.get("token"),
                repeat=int(jd.get("repeat", 3)),
                consistency_threshold=float(jd.get("consistency_threshold", 0.1)),
            ),
            fingerprint=FingerprintConfig(
                db_path=fp_db_path,
                speed_tolerance=float(fp_d.get("speed_tolerance", 0.5)),
            ),
            thresholds=ThresholdConfig(
                clip_cosine_min=float(th_d.get("clip_cosine_min", 0.90)),
                color_correlation_min=float(th_d.get("color_correlation_min", 0.85)),
                success_rate_diff_max=float(th_d.get("success_rate_diff_max", 0.10)),
                latency_ratio_max=float(th_d.get("latency_ratio_max", 2.0)),
            ),
            budget=BudgetConfig(
                max_cost_usd=float(bd["max_cost_usd"]) if bd.get("max_cost_usd") is not None else None,
                warn_cost_usd=float(bd["warn_cost_usd"]) if bd.get("warn_cost_usd") is not None else None,
                cost_per_generation=float(bd.get("cost_per_generation", 0.04)),
                cost_per_judge_call=float(bd.get("cost_per_judge_call", 0.005)),
            ),
            test_prompts=d.get("test_prompts") or list(DEFAULT_TEST_PROMPTS),
            output_dir=str(d.get("output_dir", "image-canary-results")),
            request_timeout_sec=float(d.get("request_timeout_sec", 300.0)),
            concurrency=int(d.get("concurrency", 2)),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.vendor and self.gateway.pin_channel_id:
            from urllib.parse import urlparse
            host = urlparse(self.vendor.base_url).hostname or ""
            if host in _VENDOR_HOSTS:
                pass  # vendor is external, pin_channel_id is for gateway — OK
        if not self.gateway.model:
            raise ValueError("gateway.model is required")
