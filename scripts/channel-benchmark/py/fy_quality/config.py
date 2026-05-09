"""Config for fy-quality. Separate from fy_loadtest's config because the
axes are different: we iterate over multiple CHANNELS (each with its own
sk- token or model-override), and we need judge + embedding settings.
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
class Channel:
    """One channel to test. `model` is sent as the request body's model;
    Fy-api uses the user token's group to pick the actual upstream channel.

    To pin a SPECIFIC Fy-api channel id rather than letting the distributor
    choose, set `pin_channel_id` to the integer channel id. The tool will send
    `Authorization: Bearer <token>-<pin_channel_id>` — Fy-api parses this in
    middleware/auth.go (~line 431) as a forced channel selection. This
    requires `token` to belong to an admin user; non-admin tokens with the
    suffix get a 403 from the gateway.
    """

    name: str
    model: str
    token: str
    base_url: str
    pin_channel_id: int | None = None


@dataclass
class Judge:
    label: str
    base_url: str
    api_key: str
    model: str


@dataclass
class Embedding:
    base_url: str
    api_key: str
    model: str = "text-embedding-3-small"


@dataclass
class QualityConfig:
    channels: list[Channel]
    dataset: str                            # path to JSONL
    judges: list[Judge] = field(default_factory=list)
    embedding: Embedding | None = None
    request_timeout_sec: float = 120.0
    concurrency: int = 4                    # parallel (channel, prompt) calls
    output_dir: str = "quality-results"
    pass_score: int = 4                     # rubric: both judges must hit this
    similarity_threshold: float = 0.80
    cache_dir: str = ".cache-quality"

    @classmethod
    def load(cls, path: str | Path) -> QualityConfig:
        raw = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(_expand_env(raw)) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, d: dict) -> QualityConfig:
        channels_raw = d.get("channels") or []
        if not channels_raw:
            raise ValueError("quality config requires at least one channel")
        channels = [
            Channel(
                name=str(c["name"]),
                model=str(c["model"]),
                token=str(c["token"]),
                base_url=str(c["base_url"]),
                pin_channel_id=(
                    int(c["pin_channel_id"]) if c.get("pin_channel_id") is not None else None
                ),
            )
            for c in channels_raw
        ]
        for c in channels:
            if c.pin_channel_id is not None and c.pin_channel_id <= 0:
                raise ValueError(
                    f"channel {c.name!r}: pin_channel_id must be > 0, got {c.pin_channel_id}"
                )

        judges_raw = d.get("judges") or []
        judges = [
            Judge(
                label=str(j["label"]),
                base_url=str(j["base_url"]),
                api_key=str(j["api_key"]),
                model=str(j["model"]),
            )
            for j in judges_raw
        ]

        emb = None
        emb_raw = d.get("embedding")
        if emb_raw:
            emb = Embedding(
                base_url=str(emb_raw["base_url"]),
                api_key=str(emb_raw["api_key"]),
                model=str(emb_raw.get("model", "text-embedding-3-small")),
            )

        if "dataset" not in d:
            raise ValueError("quality config requires 'dataset' (path to JSONL)")

        return cls(
            channels=channels,
            dataset=str(d["dataset"]),
            judges=judges,
            embedding=emb,
            request_timeout_sec=float(d.get("request_timeout_sec", 120.0)),
            concurrency=int(d.get("concurrency", 4)),
            output_dir=str(d.get("output_dir", "quality-results")),
            pass_score=int(d.get("pass_score", 4)),
            similarity_threshold=float(d.get("similarity_threshold", 0.80)),
            cache_dir=str(d.get("cache_dir", ".cache-quality")),
        )

    def validate(self) -> None:
        # Ensure we can grade every grader type we expect.
        if not self.channels:
            raise ValueError("at least one channel required")
        for ch in self.channels:
            if not ch.token:
                raise ValueError(f"channel {ch.name!r} missing token")
            if not ch.base_url:
                raise ValueError(f"channel {ch.name!r} missing base_url")
