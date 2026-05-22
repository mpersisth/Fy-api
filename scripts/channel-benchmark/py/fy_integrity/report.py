"""Report generation for integrity probe results."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .probes.base import ProbeResult


def write_reports(
    results: list[ProbeResult],
    *,
    config_summary: dict,
    formats: list[str],
    output_dir: str,
) -> list[Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    written: list[Path] = []

    report_data = _build_report_data(results, config_summary)

    if "json" in formats:
        p = out / f"integrity_{ts}.json"
        p.write_text(json.dumps(report_data, indent=2, ensure_ascii=False))
        written.append(p)

    if "markdown" in formats:
        p = out / f"integrity_{ts}.md"
        p.write_text(_render_markdown(report_data), encoding="utf-8")
        written.append(p)

    return written


def _build_report_data(results: list[ProbeResult], config_summary: dict) -> dict:
    passed = sum(1 for r in results if r.passed)
    failed = [r for r in results if not r.passed]
    critical = sum(1 for r in failed if r.severity == "critical")
    warning = sum(1 for r in failed if r.severity == "warning")

    return {
        "version": "0.1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": config_summary,
        "verdict": "PASS" if not failed else "FAIL",
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(failed),
            "critical": critical,
            "warning": warning,
        },
        "probes": [asdict(r) for r in results],
    }


def _render_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Channel Integrity Report\n")
    v = data["verdict"]
    s = data["summary"]
    lines.append(f"## Verdict: {v}\n")
    if v == "FAIL":
        parts = []
        if s["critical"]:
            parts.append(f"{s['critical']} critical")
        if s["warning"]:
            parts.append(f"{s['warning']} warning")
        lines.append(f"- {s['failed']} findings ({', '.join(parts)})")
    else:
        lines.append("- All probes passed")
    cfg = data["config"]
    lines.append(f"- Model: {cfg.get('model', 'N/A')}")
    if cfg.get("pin_channel_id"):
        lines.append(f"- Channel: {cfg['pin_channel_id']}")
    lines.append(f"- Generated: {data['generated_at']}\n")

    lines.append("## Summary\n")
    lines.append("| Probe | Status | Severity | Finding |")
    lines.append("|-------|--------|----------|---------|")
    for p in data["probes"]:
        status = "PASS" if p["passed"] else "FAIL"
        lines.append(
            f"| {p['probe_name']} | {status} | {p['severity']} | {p['summary']} |"
        )
    lines.append("")

    failed_probes = [p for p in data["probes"] if not p["passed"]]
    if failed_probes:
        lines.append("## Details\n")
        for p in failed_probes:
            sev = p["severity"].upper()
            lines.append(f"### {p['probe_name']} ({sev})\n")
            lines.append(f"**Finding:** {p['summary']}\n")
            if p.get("details"):
                lines.append("**Details:**\n")
                lines.append("```json")
                lines.append(json.dumps(p["details"], indent=2, ensure_ascii=False))
                lines.append("```\n")
            if p.get("evidence"):
                lines.append(f"**Evidence** ({len(p['evidence'])} entries):\n")
                lines.append("```json")
                lines.append(json.dumps(p["evidence"][:5], indent=2, ensure_ascii=False))
                if len(p["evidence"]) > 5:
                    lines.append(f"  ... ({len(p['evidence']) - 5} more entries)")
                lines.append("```\n")

    return "\n".join(lines)
