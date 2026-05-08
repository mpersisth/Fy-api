"""Config for fy-canary. One channel per run — each baseline is per-channel.
Run baseline mode once against the trusted source (the real API), then audit
mode against the gateway channel you want to verify.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(raw: str) -> str:
    missing: list[str] = []

    def _line(line: str) -> str:
        if line.lstrip().startswith("#"):
            return line

        def _sub(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            missing.append(name)
            return ""

        return _ENV_RE.sub(_sub, line)

    out = "\n".join(_line(line) for line in raw.splitlines())
    if missing:
        raise ValueError(f"undefined env vars: {sorted(set(missing))}")
    return out


@dataclass
class CanarySource:
    """The endpoint being probed (either trusted-for-baseline or suspect)."""
    name: str
    base_url: str
    api_key: str
    model: str


@dataclass
class EmbeddingRef:
    base_url: str
    api_key: str
    model: str = "text-embedding-3-small"


@dataclass
class CanaryConfig:
    source: CanarySource
    dataset: str                      # path to canary JSONL
    baselines_dir: str                # where to read/write per-channel baselines
    output_dir: str = "canary-results"
    embedding: EmbeddingRef | None = None
    mmd_enabled: bool = False         # requires model-equality-testing installed
    mmd_n_samples: int = 10           # samples per prompt for MMD
    request_timeout_sec: float = 120.0
    concurrency: int = 4

    @classmethod
    def load(cls, path: str | Path) -> CanaryConfig:
        raw = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(_expand_env(raw)) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, d: dict) -> CanaryConfig:
        s = d.get("source")
        if not s:
            raise ValueError("canary config requires 'source'")
        source = CanarySource(
            name=str(s["name"]), base_url=str(s["base_url"]),
            api_key=str(s["api_key"]), model=str(s["model"]),
        )
        emb = None
        if d.get("embedding"):
            e = d["embedding"]
            emb = EmbeddingRef(
                base_url=str(e["base_url"]),
                api_key=str(e["api_key"]),
                model=str(e.get("model", "text-embedding-3-small")),
            )
        if "dataset" not in d:
            raise ValueError("canary config requires 'dataset' path")
        return cls(
            source=source,
            dataset=str(d["dataset"]),
            baselines_dir=str(d.get("baselines_dir", "canary-baselines")),
            output_dir=str(d.get("output_dir", "canary-results")),
            embedding=emb,
            mmd_enabled=bool(d.get("mmd_enabled", False)),
            mmd_n_samples=int(d.get("mmd_n_samples", 10)),
            request_timeout_sec=float(d.get("request_timeout_sec", 120.0)),
            concurrency=int(d.get("concurrency", 4)),
        )

    def validate(self) -> None:
        if not self.source.api_key:
            raise ValueError("source.api_key is required")
        if not self.source.base_url:
            raise ValueError("source.base_url is required")
