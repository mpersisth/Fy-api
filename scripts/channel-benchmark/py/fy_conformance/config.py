"""Configuration loader for fy-conformance.

YAML schema (see conformance.yaml example):

    gateway:
      base_url: "${FY_API_URL:-http://localhost:3000}"
      user_token: "${FY_API_USER_TOKEN}"
    target:
      model: claude-sonnet-4-5
      baseline_request:
        # request body that's known-valid for the model. Each conformance case
        # mutates one field of this baseline.
        messages:
          - {role: user, content: "ping"}
        max_tokens: 16
        temperature: 0
    dataset: fy_conformance/datasets/public/conformance.jsonl
    concurrency: 4
    request_timeout_sec: 30
    output_dir: conformance-results
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def _interpolate(value: Any) -> Any:
    """Recursively expand ${VAR} / ${VAR:-default} in strings."""
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            var, default = m.group(1), m.group(2)
            return os.environ.get(var, default if default is not None else "")
        return ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


@dataclass
class GatewayCfg:
    base_url: str
    user_token: str


@dataclass
class TargetCfg:
    model: str
    baseline_request: dict


@dataclass
class Config:
    gateway: GatewayCfg
    target: TargetCfg
    dataset: Path
    output_dir: Path
    concurrency: int = 4
    request_timeout_sec: float = 30.0
    extra_headers: dict = field(default_factory=dict)


def load(path: str | os.PathLike) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    raw = _interpolate(raw)

    gw = raw.get("gateway") or {}
    if not gw.get("base_url"):
        raise ValueError("gateway.base_url is required")
    if not gw.get("user_token"):
        raise ValueError(
            "gateway.user_token is required (use ${FY_API_USER_TOKEN} env)"
        )

    tgt = raw.get("target") or {}
    if not tgt.get("model"):
        raise ValueError("target.model is required")
    baseline = tgt.get("baseline_request") or {}
    # `model` and `messages` are mandatory for /v1/chat/completions
    baseline.setdefault("model", tgt["model"])
    baseline.setdefault("messages", [{"role": "user", "content": "ping"}])

    dataset = Path(raw.get("dataset", "fy_conformance/datasets/public/conformance.jsonl"))
    if not dataset.is_absolute():
        dataset = (Path(path).resolve().parent / dataset).resolve()

    out = Path(raw.get("output_dir", "conformance-results"))

    return Config(
        gateway=GatewayCfg(base_url=gw["base_url"].rstrip("/"), user_token=gw["user_token"]),
        target=TargetCfg(model=tgt["model"], baseline_request=baseline),
        dataset=dataset,
        output_dir=out,
        concurrency=int(raw.get("concurrency", 4)),
        request_timeout_sec=float(raw.get("request_timeout_sec", 30.0)),
        extra_headers=raw.get("extra_headers") or {},
    )
