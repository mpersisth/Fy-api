"""Report writers for quality runs.

We produce:
  - JSON (full per-prompt results, programmatic)
  - CSV (one row per (channel, prompt), spreadsheet-friendly)
  - Markdown summary (per-channel scorecard + per-category breakdown)
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .runner import PromptResult, QualityReport, report_to_dict


def write_reports(r: QualityReport, formats: list[str], out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    written: list[Path] = []
    for fmt in formats:
        if fmt == "json":
            written.append(_json(r, out, ts))
        elif fmt == "csv":
            written.append(_csv(r, out, ts))
        elif fmt == "markdown":
            written.append(_md(r, out, ts))
        else:
            raise ValueError(f"unknown export format: {fmt!r}")
    return written


def _json(r: QualityReport, out: Path, ts: str) -> Path:
    p = out / f"quality_{ts}.json"
    p.write_text(json.dumps(report_to_dict(r), indent=2, ensure_ascii=False))
    return p


_CSV_HEADER = [
    "channel", "model", "prompt_id", "category", "grader",
    "passed", "score", "detail", "output_tokens", "prompt_tokens",
    "judge_tokens", "elapsed_s", "cached", "error",
]


def _csv(r: QualityReport, out: Path, ts: str) -> Path:
    p = out / f"quality_{ts}.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_CSV_HEADER)
        for pr in r.per_prompt:
            w.writerow([
                pr.channel, pr.model, pr.prompt_id, pr.category, pr.grader,
                "1" if pr.passed else "0",
                f"{pr.score:.3f}",
                pr.detail,
                pr.output_tokens, pr.prompt_tokens, pr.judge_tokens,
                f"{pr.elapsed_s:.2f}", "1" if pr.cached else "0", pr.error,
            ])
    return p


def _md(r: QualityReport, out: Path, ts: str) -> Path:
    p = out / f"quality_{ts}.md"
    lines: list[str] = []
    lines.append("# Quality scorecard")
    lines.append("")
    lines.append(f"- Generated: {datetime.fromtimestamp(r.generated_at_unix, timezone.utc).isoformat()}")
    lines.append(f"- Dataset: `{r.dataset_path}`")
    lines.append(f"- Channels: {', '.join(r.channels)}")
    lines.append("")

    # Overall per-channel pass rate.
    per_channel: dict[str, list[PromptResult]] = defaultdict(list)
    for pr in r.per_prompt:
        per_channel[pr.channel].append(pr)

    lines.append("## Overall")
    lines.append("")
    lines.append("| Channel | Pass | Total | Pass Rate | Avg Score | Judge Tokens |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for ch, rows in per_channel.items():
        ok = sum(1 for x in rows if x.passed)
        total = len(rows)
        avg_score = sum(x.score for x in rows) / total if total else 0.0
        judge_tok = sum(x.judge_tokens for x in rows)
        rate = 100.0 * ok / total if total else 0.0
        lines.append(
            f"| {ch} | {ok} | {total} | {rate:.1f}% | {avg_score:.3f} | {judge_tok} |"
        )

    # Per-category breakdown.
    lines.append("")
    lines.append("## Per-category pass rate")
    lines.append("")
    categories: list[str] = sorted({pr.category for pr in r.per_prompt})
    header = "| Channel | " + " | ".join(categories) + " |"
    sep = "|---|" + "---:|" * len(categories)
    lines.append(header)
    lines.append(sep)
    for ch, rows in per_channel.items():
        by_cat: dict[str, list[PromptResult]] = defaultdict(list)
        for pr in rows:
            by_cat[pr.category].append(pr)
        cells: list[str] = [ch]
        for cat in categories:
            cat_rows = by_cat.get(cat, [])
            if not cat_rows:
                cells.append("—")
            else:
                ok = sum(1 for x in cat_rows if x.passed)
                cells.append(f"{ok}/{len(cat_rows)}")
        lines.append("| " + " | ".join(cells) + " |")

    # Failing prompts table — most useful signal for a regression report.
    failed: list[PromptResult] = [pr for pr in r.per_prompt if not pr.passed]
    if failed:
        lines.append("")
        lines.append("## Failures")
        lines.append("")
        lines.append("| Channel | Prompt | Grader | Detail |")
        lines.append("|---|---|---|---|")
        for pr in failed:
            detail = pr.detail.replace("|", "\\|")
            if len(detail) > 120:
                detail = detail[:117] + "..."
            lines.append(f"| {pr.channel} | `{pr.prompt_id}` | {pr.grader} | {detail} |")

    p.write_text("\n".join(lines) + "\n")
    return p
