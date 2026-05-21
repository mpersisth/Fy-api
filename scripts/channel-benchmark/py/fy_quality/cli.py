"""fy-quality CLI entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console

from . import __version__
from .config import QualityConfig
from .report import write_reports
from .runner import QualityRunner


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fy-quality",
        description="Golden-prompt quality evaluation for Fy-api channels.",
    )
    p.add_argument("-c", "--config", default="quality.yaml")
    p.add_argument("--dataset", help="Override dataset path (JSONL)")
    p.add_argument("--output", help="Override output directory")
    p.add_argument("--concurrency", type=int, help="Override concurrency")
    p.add_argument("--dry-run", action="store_true", help="Validate config only")
    p.add_argument(
        "--formats",
        default="json,csv,markdown,pdf",
        help="Comma-separated: json,csv,markdown,pdf",
    )
    p.add_argument("-V", "--version", action="version", version=f"fy-quality {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()

    try:
        cfg = QualityConfig.load(args.config)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]config: {e}[/red]")
        return 2
    if args.dataset:
        cfg.dataset = args.dataset
    if args.output:
        cfg.output_dir = args.output
    if args.concurrency:
        cfg.concurrency = args.concurrency
    try:
        cfg.validate()
    except ValueError as e:
        console.print(f"[red]config invalid: {e}[/red]")
        return 2

    formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    console.print(f"[bold]Channels:[/]    {[c.name for c in cfg.channels]}")
    console.print(f"[bold]Dataset:[/]     {cfg.dataset}")
    console.print(f"[bold]Judges:[/]      {[j.label for j in cfg.judges] or '— (deterministic graders only)'}")
    console.print(f"[bold]Embedding:[/]   {'enabled' if cfg.embedding else 'disabled (no similarity grader)'}")
    console.print(f"[bold]Concurrency:[/] {cfg.concurrency}")
    console.print(f"[bold]Output:[/]      {cfg.output_dir} ({formats})")

    if args.dry_run:
        console.print("\n[cyan](dry-run: config valid, no requests sent)[/cyan]")
        return 0

    try:
        report = asyncio.run(QualityRunner(cfg).run())
    except KeyboardInterrupt:
        console.print("[yellow]interrupted[/yellow]")
        return 130

    files = write_reports(report, formats, cfg.output_dir)
    console.rule("[bold green]done")
    total = len(report.per_prompt)
    ok = sum(1 for p in report.per_prompt if p.passed)
    console.print(f"graded {total} prompts, {ok} passed ({100.0*ok/total if total else 0:.1f}%)")
    for f in files:
        console.print(f"wrote {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
