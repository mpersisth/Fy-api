"""Per-channel baseline storage.

A baseline is the trusted reference set of outputs / embeddings against
which future probes are compared. Store on disk as plain JSON — no
database, no schema tooling, just a Path.

Schema versioning:
- v1 (legacy, pre-2026-05): {source_name, model, created_at_unix, probes}
- v2 (current):             v1 + {schema_version, recorded_at_iso, n_probes,
                                  total_samples, fy_canary_version}

v1 files load fine via from_dict (missing fields default sensibly). New
saves always write v2 so health checks have what they need on next load.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

CURRENT_SCHEMA_VERSION = 2


@dataclass
class ProbeBaseline:
    """Per-prompt baseline data."""
    prompt_id: str
    method: str                    # 'alignment' | 'drift' | 'mmd'
    samples: list[str] = field(default_factory=list)  # raw text, multi-sample for MMD/drift
    centroid: list[float] | None = None                # drift: embedding centroid
    # Alignment stores just the first sample by convention.

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ProbeBaseline:
        return cls(
            prompt_id=str(d["prompt_id"]),
            method=str(d["method"]),
            samples=list(d.get("samples", [])),
            centroid=d.get("centroid"),
        )


@dataclass
class ChannelBaseline:
    """All probes for one source (name-keyed). Lives in
    `<baselines_dir>/<source_name>.json`.

    Health metadata (recorded_at_iso, n_probes, total_samples,
    fy_canary_version) is written every save and used by the audit command
    to flag stale baselines.
    """
    source_name: str
    model: str
    created_at_unix: float
    probes: dict[str, ProbeBaseline] = field(default_factory=dict)  # prompt_id -> baseline

    # v2 health metadata. Optional for backwards compat with v1 files;
    # recompute on load if missing.
    schema_version: int = CURRENT_SCHEMA_VERSION
    recorded_at_iso: str = ""
    fy_canary_version: str = ""

    @property
    def n_probes(self) -> int:
        return len(self.probes)

    @property
    def total_samples(self) -> int:
        return sum(len(p.samples) for p in self.probes.values())

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.created_at_unix)

    @property
    def age_days(self) -> float:
        return self.age_seconds / 86400.0

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "source_name": self.source_name,
            "model": self.model,
            "created_at_unix": self.created_at_unix,
            "recorded_at_iso": self.recorded_at_iso or _iso(self.created_at_unix),
            "fy_canary_version": self.fy_canary_version or __version__,
            "n_probes": self.n_probes,
            "total_samples": self.total_samples,
            "probes": {k: v.as_dict() for k, v in self.probes.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> ChannelBaseline:
        created = float(d["created_at_unix"])
        return cls(
            source_name=str(d["source_name"]),
            model=str(d["model"]),
            created_at_unix=created,
            probes={
                k: ProbeBaseline.from_dict(v)
                for k, v in (d.get("probes") or {}).items()
            },
            schema_version=int(d.get("schema_version", 1)),
            recorded_at_iso=str(d.get("recorded_at_iso") or _iso(created)),
            fy_canary_version=str(d.get("fy_canary_version") or ""),
        )


def _iso(unix_seconds: float) -> str:
    return datetime.fromtimestamp(unix_seconds, timezone.utc).isoformat(
        timespec="seconds",
    )


class BaselineStore:
    def __init__(self, root: str | Path):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def path_for(self, source_name: str) -> Path:
        # Light sanitization: only filename-friendly chars.
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in source_name)
        return self._root / f"{safe}.json"

    def load(self, source_name: str) -> ChannelBaseline | None:
        p = self.path_for(source_name)
        if not p.exists():
            return None
        return ChannelBaseline.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save(self, baseline: ChannelBaseline) -> Path:
        # Ensure v2 metadata is current at write time.
        baseline.schema_version = CURRENT_SCHEMA_VERSION
        baseline.recorded_at_iso = _iso(baseline.created_at_unix)
        baseline.fy_canary_version = __version__
        p = self.path_for(baseline.source_name)
        p.write_text(json.dumps(baseline.as_dict(), indent=2, ensure_ascii=False))
        return p
