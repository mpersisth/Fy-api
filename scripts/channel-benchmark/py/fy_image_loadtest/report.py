"""Report writers for fy-image-loadtest."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .runner import ChannelStats, SuiteResult


def write_reports(result: SuiteResult, formats: list[str], out_dir: str | Path) -> list[Path]:
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


def _write_json(result: SuiteResult, out: Path, ts: str) -> Path:
    path = out / f"image_loadtest_{ts}.json"
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gateway": result.base_url,
        "model": result.model,
        "prompt": result.prompt,
        "size": result.size,
        "quality": result.quality,
        "n": result.n,
        "concurrency_per_channel": result.concurrency_per_channel,
        "stopped_reason": result.stopped_reason,
        "channels": [_channel_json(ch) for ch in result.channels],
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    return path


def _channel_json(ch: ChannelStats) -> dict[str, object]:
    return {
        "name": ch.name,
        "pin_channel_id": ch.pin_channel_id,
        "total": ch.total,
        "ok": ch.ok,
        "failed": ch.failed,
        "success_rate_pct": ch.success_rate_pct(),
        "images": ch.images,
        "requests_per_min": ch.requests_per_min(),
        "images_per_min": ch.images_per_min(),
        "e2e_p50_ms": ch.e2e_p50_ms(),
        "e2e_p95_ms": ch.e2e_p95_ms(),
        "e2e_p99_ms": ch.e2e_p99_ms(),
        "avg_response_kib": ch.avg_response_kib(),
        "has_b64_json_ok": ch.has_b64_json_ok,
        "has_url_ok": ch.has_url_ok,
        "revised_prompt_hits": ch.revised_prompt_hits,
        "top_error": ch.top_error(),
        "status_codes": dict(ch.status_codes),
        "error_breakdown": dict(ch.error_breakdown),
    }


def _write_csv(result: SuiteResult, out: Path, ts: str) -> Path:
    path = out / f"image_loadtest_{ts}.csv"
    with path.open("w", newline="") as f:
        f.write(
            f"# model={result.model} gateway={result.base_url} size={result.size} quality={result.quality} n={result.n}\n"
        )
        w = csv.writer(f)
        w.writerow([
            "channel",
            "pin_channel_id",
            "total",
            "ok",
            "failed",
            "success_rate_pct",
            "images",
            "requests_per_min",
            "images_per_min",
            "e2e_p50_ms",
            "e2e_p95_ms",
            "e2e_p99_ms",
            "avg_response_kib",
            "has_b64_json_ok",
            "has_url_ok",
            "revised_prompt_hits",
            "top_error",
        ])
        for ch in result.channels:
            w.writerow([
                ch.name,
                ch.pin_channel_id,
                ch.total,
                ch.ok,
                ch.failed,
                f"{ch.success_rate_pct():.1f}",
                ch.images,
                f"{ch.requests_per_min():.2f}",
                f"{ch.images_per_min():.2f}",
                f"{ch.e2e_p50_ms():.1f}",
                f"{ch.e2e_p95_ms():.1f}",
                f"{ch.e2e_p99_ms():.1f}",
                f"{ch.avg_response_kib():.1f}",
                ch.has_b64_json_ok,
                ch.has_url_ok,
                ch.revised_prompt_hits,
                ch.top_error(),
            ])
    return path


def _write_md(result: SuiteResult, out: Path, ts: str) -> Path:
    path = out / f"image_loadtest_{ts}.md"
    lines = [
        f"# Image load test: {result.model}",
        "",
        f"- Gateway: `{result.base_url}`",
        f"- Size / Quality / N: `{result.size}` / `{result.quality}` / `{result.n}`",
        f"- Concurrency per channel: `{result.concurrency_per_channel}`",
        f"- Stopped: `{result.stopped_reason}`",
        "",
        "| Channel | ID | OK/Total | Succ% | Images | RPM | IPM | E2E p50/p95/p99 (ms) | Avg resp (KiB) | Payload |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for ch in result.channels:
        payload = []
        if ch.has_b64_json_ok:
            payload.append(f"b64:{ch.has_b64_json_ok}")
        if ch.has_url_ok:
            payload.append(f"url:{ch.has_url_ok}")
        if ch.revised_prompt_hits:
            payload.append(f"revised:{ch.revised_prompt_hits}")
        lines.append(
            "| {name} | {id} | {ok}/{total} | {succ:.1f}% | {images} | {rpm:.2f} | {ipm:.2f} | {p50:.0f}/{p95:.0f}/{p99:.0f} | {resp:.1f} | {payload} |".format(
                name=ch.name,
                id=ch.pin_channel_id,
                ok=ch.ok,
                total=ch.total,
                succ=ch.success_rate_pct(),
                images=ch.images,
                rpm=ch.requests_per_min(),
                ipm=ch.images_per_min(),
                p50=ch.e2e_p50_ms(),
                p95=ch.e2e_p95_ms(),
                p99=ch.e2e_p99_ms(),
                resp=ch.avg_response_kib(),
                payload=", ".join(payload) or "-",
            )
        )
    if any(ch.error_breakdown for ch in result.channels):
        lines.extend(["", "## Errors", "", "| Channel | Error signature | Count |", "|---|---|---:|"])
        for ch in result.channels:
            for sig, count in sorted(ch.error_breakdown.items(), key=lambda kv: -kv[1]):
                trim = sig.replace("|", "\\|")
                if len(trim) > 120:
                    trim = trim[:117] + "..."
                lines.append(f"| {ch.name} | `{trim}` | {count} |")
    path.write_text("\n".join(lines) + "\n")
    return path
