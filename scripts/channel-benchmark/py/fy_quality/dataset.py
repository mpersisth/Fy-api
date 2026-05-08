"""JSONL dataset loader for quality and canary prompts.

The two tools share a single JSONL schema so prompt data is authored once
and consumed in both contexts.  A line belongs to the tool that owns its
`kind` field.

Schema (lenient — extra fields are preserved):

    {
      "id": "math-01",              # unique string
      "kind": "quality" | "canary", # which tool processes this row
      "prompt": "...",              # required
      "system": "...",              # optional system prompt
      "category": "math",           # freeform grouping for report
      "grader": "exact" | "regex" | "contains" | "json_schema"
              | "rubric" | "similarity" | "pairwise",
      "expected": "...",            # meaning depends on grader
      "rubric": "...",              # only for grader=rubric
      "reference": "...",           # only for grader=similarity / pairwise
      "max_tokens": 256,            # per-prompt override (optional)
      "temperature": 0.0,           # per-prompt override (optional)
    }

For canary rows:
    {
      "id": "fp-mmd-tiger-01",
      "kind": "canary",
      "method": "mmd" | "alignment" | "drift" | "selfid",
      "prompt": "...",
      "n_samples": 20,              # samples per prompt for mmd/drift
      "temperature": 1.0,           # mmd needs temperature > 0
      "max_tokens": 200,
    }
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PromptRow:
    """A single JSONL row. Raw keeps unknown fields for forward compat."""

    id: str
    kind: str
    prompt: str
    raw: dict = field(default_factory=dict)

    # Frequently accessed fields promoted for convenience.
    system: str | None = None
    category: str = "uncategorized"
    grader: str = ""
    expected: str | None = None
    rubric: str | None = None
    reference: str | None = None
    method: str = ""
    max_tokens: int | None = None
    temperature: float | None = None
    n_samples: int | None = None

    @classmethod
    def from_json(cls, obj: dict) -> PromptRow:
        if "id" not in obj:
            raise ValueError(f"dataset row missing 'id': {obj!r}")
        if "prompt" not in obj:
            raise ValueError(f"dataset row {obj.get('id')!r} missing 'prompt'")
        kind = obj.get("kind", "quality")
        if kind not in {"quality", "canary"}:
            raise ValueError(f"dataset row {obj['id']!r} has unknown kind={kind!r}")
        return cls(
            id=str(obj["id"]),
            kind=kind,
            prompt=str(obj["prompt"]),
            raw=obj,
            system=obj.get("system"),
            category=str(obj.get("category", "uncategorized")),
            grader=str(obj.get("grader", "")),
            expected=obj.get("expected"),
            rubric=obj.get("rubric"),
            reference=obj.get("reference"),
            method=str(obj.get("method", "")),
            max_tokens=obj.get("max_tokens"),
            temperature=obj.get("temperature"),
            n_samples=obj.get("n_samples"),
        )


def load_jsonl(path: str | Path, *, kind: str | None = None) -> list[PromptRow]:
    """Load a JSONL file; optionally filter to a single kind.

    Blank lines and lines starting with '#' are ignored for human-edited files.
    """
    p = Path(path)
    rows: list[PromptRow] = []
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ValueError(f"{p}:{lineno} invalid JSON: {e}") from e
        row = PromptRow.from_json(obj)
        if kind is not None and row.kind != kind:
            continue
        rows.append(row)
    _check_unique_ids(rows, p)
    return rows


def _check_unique_ids(rows: Iterable[PromptRow], path: Path) -> None:
    seen: set[str] = set()
    dupes: list[str] = []
    for r in rows:
        if r.id in seen:
            dupes.append(r.id)
        seen.add(r.id)
    if dupes:
        raise ValueError(f"{path}: duplicate ids: {sorted(set(dupes))}")
