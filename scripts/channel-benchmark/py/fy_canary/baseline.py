"""Per-channel baseline storage.

A baseline is the trusted reference set of outputs / embeddings against
which future probes are compared. Store on disk as plain JSON — no
database, no schema tooling, just a Path.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


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
    """
    source_name: str
    model: str
    created_at_unix: float
    probes: dict[str, ProbeBaseline] = field(default_factory=dict)  # prompt_id -> baseline

    def as_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "model": self.model,
            "created_at_unix": self.created_at_unix,
            "probes": {k: v.as_dict() for k, v in self.probes.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> ChannelBaseline:
        return cls(
            source_name=str(d["source_name"]),
            model=str(d["model"]),
            created_at_unix=float(d["created_at_unix"]),
            probes={k: ProbeBaseline.from_dict(v) for k, v in (d.get("probes") or {}).items()},
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
        p = self.path_for(baseline.source_name)
        p.write_text(json.dumps(baseline.as_dict(), indent=2, ensure_ascii=False))
        return p
