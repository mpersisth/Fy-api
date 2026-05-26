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
    lines.append("| Channel | Model | Avail | Perf | Quality | Auth | Comply | Composite | Grade |")
    lines.append("|---------|-------|-------|------|---------|------|--------|-----------|-------|")
    for c in sorted(cards, key=lambda x: x.composite_score, reverse=True):
        dims = c.dimensions

        def _val(name: str) -> str:
            d = dims.get(name)
            return f"{d.score:.0f}" if d and d.available else "N/A"

        flags = f" {''.join(c.flags)}" if c.flags else ""
        lines.append(
            f"| {c.channel_name} | {c.model} | {_val('availability')} | {_val('performance')} "
            f"| {_val('quality')} | {_val('authenticity')} | {_val('compliance')} "
            f"| {c.composite_score:.1f} | **{c.grade}**{flags} |"
        )
    lines.append("")

    # Detail section
    for c in sorted(cards, key=lambda x: x.composite_score, reverse=True):
        lines.append(f"## {c.channel_name} / {c.model}")
        lines.append("")
        for name, dim in c.dimensions.items():
            status = "✓" if dim.available else "—"
            lines.append(f"- **{name}** ({dim.weight:.0%}): {dim.score:.1f}/100 {status} | {dim.detail}")
        if c.flags:
            lines.append("")
            for flag in c.flags:
                lines.append(f"- ⚠ {flag}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
