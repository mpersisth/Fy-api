"""Output scorecard as JSON and Markdown."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .scorer import ChannelScorecard


def scorecard_to_dict(card: ChannelScorecard) -> dict:
    dims = {}
    for name, dim in card.dimensions.items():
        dims[name] = {
            "score": round(dim.score, 1),
            "weight": dim.weight,
            "detail": dim.detail,
            "available": dim.available,
        }
    return {
        "channel_name": card.channel_name,
        "channel_id": card.channel_id,
        "model": card.model,
        "grade": card.grade,
        "composite_score": round(card.composite_score, 1),
        "dimensions": dims,
        "flags": card.flags,
        "gated_out": card.gated_out,
    }


def write_json(cards: list[ChannelScorecard], path: Path) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channels": [scorecard_to_dict(c) for c in cards],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_markdown(cards: list[ChannelScorecard], path: Path) -> None:
    lines: list[str] = ["# Channel Scorecard", ""]
    lines.append("| Channel | Model | Avail | Perf | Quality | Auth | Composite | Grade |")
    lines.append("|---------|-------|-------|------|---------|------|-----------|-------|")
    for c in sorted(cards, key=lambda x: x.composite_score, reverse=True):
        dims = c.dimensions
        a = f"{dims['availability'].score:.0f}" if dims["availability"].available else "N/A"
        p = f"{dims['performance'].score:.0f}" if dims["performance"].available else "N/A"
        q = f"{dims['quality'].score:.0f}" if dims["quality"].available else "N/A"
        au = f"{dims['authenticity'].score:.0f}" if dims["authenticity"].available else "N/A"
        flags = f" {''.join(c.flags)}" if c.flags else ""
        lines.append(
            f"| {c.channel_name} | {c.model} | {a} | {p} | {q} | {au} "
            f"| {c.composite_score:.1f} | **{c.grade}**{flags} |"
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
