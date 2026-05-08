"""Report writers: JSON, CSV, and markdown summary table."""

from __future__ import annotations

import csv
import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

from .metrics import LevelAggregate
from .runner import RampResult


def write_reports(result: RampResult, formats: list[str], out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    written: list[Path] = []
    for fmt in formats:
        if fmt == "json":
            written.append(_write_json(result, out, ts))
        elif fmt == "csv":
            written.append(_write_csv(result, out, ts))
        elif fmt == "markdown":
            written.append(_write_md(result, out, ts))
        else:
            raise ValueError(f"unknown export format: {fmt}")
    return written


def _write_json(result: RampResult, out: Path, ts: str) -> Path:
    path = out / f"loadtest_{ts}.json"
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gateway": result.base_url,
        "model": result.model,
        "levels": [dataclasses.asdict(lv) for lv in result.levels],
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    return path


_CSV_HEADER = [
    "concurrency", "total", "ok", "failed", "success_rate_pct",
    "wall_time_s", "rps", "aggregate_tok_per_s",
    "e2e_p50_ms", "e2e_p95_ms", "e2e_p99_ms",
    "ttft_p50_ms", "ttft_p95_ms", "ttft_p99_ms",
    "itl_p50_ms", "itl_p95_ms",
    "tpot_p50_ms", "tpot_p95_ms",
    "per_req_tok_per_s_avg", "per_req_tok_per_s_p50",
    "avg_prompt_tokens", "avg_completion_tokens", "avg_cached_tokens",
    "goodput_req_per_s", "top_error",
]


def _write_csv(result: RampResult, out: Path, ts: str) -> Path:
    path = out / f"loadtest_{ts}.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_CSV_HEADER)
        for lv in result.levels:
            w.writerow([
                lv.concurrency, lv.total, lv.ok, lv.failed, f"{lv.success_rate_pct:.1f}",
                f"{lv.wall_time_s:.2f}", f"{lv.throughput_req_per_s:.2f}",
                f"{lv.aggregate_tok_per_s:.1f}",
                _fmt(lv.e2e.p50_ms), _fmt(lv.e2e.p95_ms), _fmt(lv.e2e.p99_ms),
                _fmt(lv.ttft.p50_ms), _fmt(lv.ttft.p95_ms), _fmt(lv.ttft.p99_ms),
                _fmt(lv.itl.p50_ms), _fmt(lv.itl.p95_ms),
                _fmt(lv.tpot.p50_ms), _fmt(lv.tpot.p95_ms),
                f"{lv.per_request_tok_per_s.avg:.2f}",
                f"{lv.per_request_tok_per_s.p50:.2f}",
                f"{lv.avg_prompt_tokens:.1f}",
                f"{lv.avg_completion_tokens:.1f}",
                f"{lv.avg_cached_tokens:.1f}",
                _fmt_opt(lv.goodput_req_per_s),
                _top_error(lv),
            ])
    return path


def _write_md(result: RampResult, out: Path, ts: str) -> Path:
    path = out / f"loadtest_{ts}.md"
    lines: list[str] = []
    lines.append(f"# Load test: {result.model}")
    lines.append("")
    lines.append(f"- Gateway: `{result.base_url}`")
    lines.append(f"- Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append("| Concurrency | OK/Total | Succ% | E2E p50/p95 (ms) | TTFT p50/p95 (ms) | ITL p50/p95 (ms) | RPS | Tok/s | Goodput |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for lv in result.levels:
        lines.append(
            "| {c} | {ok}/{tot} | {sr:.1f}% | {e50:.0f}/{e95:.0f} | {t50}/{t95} | {i50}/{i95} | {rps:.2f} | {ts:.1f} | {gp} |".format(
                c=lv.concurrency, ok=lv.ok, tot=lv.total, sr=lv.success_rate_pct,
                e50=lv.e2e.p50_ms, e95=lv.e2e.p95_ms,
                t50=_fmt(lv.ttft.p50_ms) or "-", t95=_fmt(lv.ttft.p95_ms) or "-",
                i50=_fmt(lv.itl.p50_ms) or "-", i95=_fmt(lv.itl.p95_ms) or "-",
                rps=lv.throughput_req_per_s, ts=lv.aggregate_tok_per_s,
                gp=_fmt_opt(lv.goodput_req_per_s) or "-",
            )
        )

    # Error summary — only if anything failed.
    has_errors = any(lv.error_breakdown for lv in result.levels)
    if has_errors:
        lines.append("")
        lines.append("## Errors")
        lines.append("")
        lines.append("| Concurrency | Error signature | Count |")
        lines.append("|---:|---|---:|")
        for lv in result.levels:
            for sig, n in sorted(lv.error_breakdown.items(), key=lambda kv: -kv[1]):
                trim = sig.replace("|", "\\|")
                if len(trim) > 120:
                    trim = trim[:117] + "..."
                lines.append(f"| {lv.concurrency} | `{trim}` | {n} |")

    path.write_text("\n".join(lines) + "\n")
    return path


def _fmt(v: float) -> str:
    return f"{v:.1f}" if v else ""


def _fmt_opt(v: float | None) -> str:
    if v is None:
        return ""
    return f"{v:.2f}"


def _top_error(lv: LevelAggregate) -> str:
    if not lv.error_breakdown:
        return ""
    sig, n = max(lv.error_breakdown.items(), key=lambda kv: kv[1])
    sig_short = sig if len(sig) <= 80 else sig[:77] + "..."
    return f"{sig_short} (x{n})"
