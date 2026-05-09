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
    """The endpoint being probed (either trusted-for-baseline or suspect).

    `pin_channel_id` is honored only when the source IS an Fy-api gateway —
    when set, the canary client appends "-{pin_channel_id}" to api_key, which
    Fy-api parses (middleware/auth.go ~line 431) as a forced channel
    selection. The api_key MUST belong to an admin user; non-admin tokens
    with the suffix get a 403 from the gateway.

    Leave `pin_channel_id` None for `baseline` runs against the trusted vendor
    API (OpenAI, Anthropic, Gemini direct). Set it for `audit` runs against
    the gateway when you want to verify ONE specific channel rather than
    whatever the distributor picks today.
    """
    name: str
    base_url: str
    api_key: str
    model: str
    pin_channel_id: int | None = None


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

    # Audit warns when the loaded baseline is older than this many days.
    # 30 is a defensible default — model providers typically version on
    # multi-week cadences and minor system-prompt drift is hard to detect
    # before then.
    baseline_max_age_days: int = 30

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
            pin_channel_id=(
                int(s["pin_channel_id"]) if s.get("pin_channel_id") is not None else None
            ),
        )
        if source.pin_channel_id is not None and source.pin_channel_id <= 0:
            raise ValueError(
                f"source.pin_channel_id must be > 0, got {source.pin_channel_id}"
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
            baseline_max_age_days=int(d.get("baseline_max_age_days", 30)),
        )

    def validate(self) -> None:
        if not self.source.api_key:
            raise ValueError("source.api_key is required")
        if not self.source.base_url:
            raise ValueError("source.base_url is required")
        # pin_channel_id is an Fy-api admin feature; setting it against a
        # vendor-direct URL (OpenAI / Anthropic / Google) will produce
        # `Bearer sk-...-26` style tokens that the vendor rejects with a
        # confusing 401. Catch this at load time rather than mid-run.
        if self.source.pin_channel_id is not None:
            host = self.source.base_url.lower()
            vendor_hosts = (
                "api.openai.com",
                "api.anthropic.com",
                "generativelanguage.googleapis.com",
                "api.deepseek.com",
                "api.moonshot.cn",
                "dashscope.aliyuncs.com",
            )
            if any(h in host for h in vendor_hosts):
                raise ValueError(
                    f"source.pin_channel_id is set ({self.source.pin_channel_id}) "
                    f"but source.base_url ({self.source.base_url}) looks like a "
                    "direct vendor API. Channel pinning is an Fy-api admin "
                    "feature; remove pin_channel_id for vendor-direct baseline "
                    "runs, and use it only when source.base_url is an Fy-api "
                    "gateway."
                )
