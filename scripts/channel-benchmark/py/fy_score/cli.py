"""CLI entrypoint for fy-score."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from . import __version__
from .loader import load_canary, load_loadtest, load_quality, load_smoke
from .report import write_json, write_markdown
from .scorer import ChannelScorecard, build_scorecard


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fy-score",
        description="SLO-anchored channel scorecard generator.",
    )
    p.add_argument("--smoke", type=Path, nargs="*", help="Go smoke-test result JSON(s)")
    p.add_argument("--loadtest", type=Path, nargs="*", help="fy-loadtest result JSON(s)")
    p.add_argument("--quality", type=Path, nargs="*", help="fy-quality result JSON(s)")
    p.add_argument("--canary", type=Path, nargs="*", help="fy-canary result JSON(s)")
    p.add_argument("--smoke-dir", type=Path, help="Directory of smoke JSONs")
    p.add_argument("--loadtest-dir", type=Path, help="Directory of loadtest JSONs")
    p.add_argument("--quality-dir", type=Path, help="Directory of quality JSONs")
    p.add_argument("--canary-dir", type=Path, help="Directory of canary JSONs")
    p.add_argument("-o", "--output", type=Path, default=Path("scorecard.json"))
    p.add_argument("--markdown", type=Path, help="Also write Markdown scorecard")
    p.add_argument("--channel-id", type=int, help="Force all data to this channel ID (useful for single-channel runs)")
    p.add_argument("--channel-name", help="Force channel display name")
    p.add_argument("--dry-run", action="store_true", help="Show discovered files and exit")
    p.add_argument("-V", "--version", action="version", version=f"fy-score {__version__}")
    return p


def _collect_files(explicit: list[Path] | None, directory: Path | None, suffix: str = ".json") -> list[Path]:
    files: list[Path] = []
    if explicit:
        files.extend(explicit)
    if directory and directory.is_dir():
        files.extend(sorted(directory.glob(f"*{suffix}")))
    return files


def _key(channel_name: str, channel_id: int | None, model: str) -> str:
    if channel_id is not None:
        return f"chid:{channel_id}||{model}"
    return f"name:{channel_name}||{model}"


def _merge(inputs: dict[str, dict], channel_name: str, channel_id: int | None, model: str) -> str:
    """Find or create the merge slot for this (channel, model) pair."""
    k_id = f"chid:{channel_id}||{model}" if channel_id is not None else None
    k_name = f"name:{channel_name}||{model}"
    if k_id and k_id in inputs:
        return k_id
    if k_name in inputs:
        return k_name
    k = k_id if k_id else k_name
    inputs[k] = {"channel_name": channel_name, "channel_id": channel_id, "model": model}
    return k


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()

    smoke_files = _collect_files(args.smoke, args.smoke_dir)
    lt_files = _collect_files(args.loadtest, args.loadtest_dir)
    qa_files = _collect_files(args.quality, args.quality_dir)
    canary_files = _collect_files(args.canary, args.canary_dir)

    if args.dry_run:
        console.print(f"[bold]Smoke:[/bold]    {[str(f) for f in smoke_files]}")
        console.print(f"[bold]Loadtest:[/bold] {[str(f) for f in lt_files]}")
        console.print(f"[bold]Quality:[/bold]  {[str(f) for f in qa_files]}")
        console.print(f"[bold]Canary:[/bold]   {[str(f) for f in canary_files]}")
        return 0

    inputs: dict[str, dict] = {}

    for f in smoke_files:
        for m in load_smoke(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["success_rate"] = m.success_rate

    for f in lt_files:
        for m in load_loadtest(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["ttft_p95_ms"] = m.ttft_p95_ms
            inputs[k]["e2e_p95_ms"] = m.e2e_p95_ms
            inputs[k]["throughput_toks"] = m.throughput_toks

    for f in qa_files:
        for m in load_quality(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["quality_pass_rate"] = m.pass_rate
            inputs[k]["quality_avg_score"] = m.avg_score

    for f in canary_files:
        for m in load_canary(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["canary_probe_pass_rate"] = m.probe_pass_rate
            inputs[k]["canary_avg_probe_score"] = m.avg_probe_score

    if not inputs:
        console.print("[red]No data found. Provide at least one result file.[/red]")
        return 2

    # When --channel-id is set, merge all entries with the same model into one slot per model
    if args.channel_id is not None:
        merged: dict[str, dict] = {}
        for info in inputs.values():
            model = info["model"]
            mk = f"chid:{args.channel_id}||{model}"
            if mk not in merged:
                merged[mk] = {
                    "channel_name": args.channel_name or info.get("channel_name", ""),
                    "channel_id": args.channel_id,
                    "model": model,
                }
            for field in ("success_rate", "ttft_p95_ms", "e2e_p95_ms", "throughput_toks",
                          "quality_pass_rate", "quality_avg_score",
                          "canary_probe_pass_rate", "canary_avg_probe_score"):
                if field in info and field not in merged[mk]:
                    merged[mk][field] = info[field]
        inputs = merged

    cards: list[ChannelScorecard] = []
    for info in inputs.values():
        card = build_scorecard(
            channel_name=info["channel_name"],
            channel_id=info.get("channel_id"),
            model=info["model"],
            success_rate=info.get("success_rate"),
            ttft_p95_ms=info.get("ttft_p95_ms"),
            e2e_p95_ms=info.get("e2e_p95_ms"),
            throughput_toks=info.get("throughput_toks"),
            quality_pass_rate=info.get("quality_pass_rate"),
            quality_avg_score=info.get("quality_avg_score"),
            canary_probe_pass_rate=info.get("canary_probe_pass_rate"),
            canary_avg_probe_score=info.get("canary_avg_probe_score"),
        )
        cards.append(card)

    write_json(cards, args.output)
    console.print(f"[green]wrote {args.output}[/green]")

    if args.markdown:
        write_markdown(cards, args.markdown)
        console.print(f"[green]wrote {args.markdown}[/green]")

    for card in sorted(cards, key=lambda c: c.composite_score, reverse=True):
        grade_color = {"A": "green", "B": "cyan", "C": "yellow", "D": "red", "F": "red bold"}.get(card.grade, "white")
        console.print(
            f"  [{grade_color}]{card.grade}[/{grade_color}] {card.composite_score:5.1f}  "
            f"{card.channel_name} / {card.model}"
        )
        if card.flags:
            for flag in card.flags:
                console.print(f"       [yellow]⚠ {flag}[/yellow]")

    return 0


if __name__ == "__main__":
    sys.exit(main())

