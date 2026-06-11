"""CLI entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console

from . import __version__
from .config import Config
from .report import write_reports, write_suite_reports
from .runner import Ramp


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fy-loadtest",
        description="Concurrency-ramp load tester for Fy-api (OpenAI-compatible).",
    )
    p.add_argument("-c", "--config", default="loadtest.yaml", help="Path to YAML config")
    p.add_argument("--base-url", help="Override gateway.base_url")
    p.add_argument("--model", help="Override load.model")
    p.add_argument(
        "--concurrencies",
        help="Override load.concurrency_levels (comma-separated, e.g. 1,5,25)",
    )
    p.add_argument("--reps", type=int, help="Override load.requests_per_level")
    p.add_argument("--warmup", type=int, help="Override load.warmup_requests")
    p.add_argument("--output", help="Override export.output_dir")
    p.add_argument(
        "--formats",
        help="Override export.formats (comma-separated: json,csv,markdown,pdf)",
    )
    p.add_argument("--dry-run", action="store_true", help="Validate config and exit")
    p.add_argument("--ceiling", action="store_true", help="Enable ceiling finder mode (measure max RPM/TPM)")
    p.add_argument("-V", "--version", action="version", version=f"fy-loadtest {__version__}")
    return p


def apply_overrides(cfg: Config, args: argparse.Namespace) -> None:
    if args.base_url:
        cfg.gateway.base_url = args.base_url
    if args.model:
        cfg.load.model = args.model
        cfg.load.models = [args.model]
    if args.concurrencies:
        cfg.load.concurrency_levels = [int(x) for x in args.concurrencies.split(",") if x.strip()]
    if args.reps:
        cfg.load.requests_per_level = args.reps
    if args.warmup is not None:
        cfg.load.warmup_requests = args.warmup
    if args.output:
        cfg.export.output_dir = args.output
    if args.formats:
        cfg.export.formats = [x.strip() for x in args.formats.split(",") if x.strip()]
    if args.ceiling:
        cfg.load.ceiling_finder.enabled = True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()

    try:
        cfg = Config.load(args.config)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]config: {e}[/red]")
        return 2

    apply_overrides(cfg, args)

    try:
        cfg.validate()
    except ValueError as e:
        console.print(f"[red]config invalid: {e}[/red]")
        return 2

    multi_model = len(cfg.load.models) > 1
    console.print(f"[bold]Gateway:[/bold]      {cfg.gateway.base_url}")
    if multi_model:
        console.print(f"[bold]Models:[/bold]       {cfg.load.models}")
    else:
        console.print(f"[bold]Model:[/bold]        {cfg.load.model}")
    if cfg.gateway.channels:
        ch_desc = ", ".join(f"{ch.name}(id={ch.pin_channel_id})" for ch in cfg.gateway.channels)
        console.print(f"[bold]Channels:[/bold]    {ch_desc}")
    if cfg.load.ceiling_finder.enabled:
        console.print(f"[bold]Ceiling:[/bold]      max_c={cfg.load.ceiling_finder.max_concurrency}, stop_429>{cfg.load.ceiling_finder.stop_429_pct}%, sustain={cfg.load.ceiling_finder.sustain_duration_s}s")
    elif cfg.load.auto_ramp.enabled:
        console.print(f"[bold]Auto-ramp:[/bold]   max={cfg.load.auto_ramp.max_concurrency}, stop<{cfg.load.auto_ramp.stop_success_pct}% success")
    else:
        console.print(f"[bold]Concurrency:[/bold]  {cfg.load.concurrency_levels}")
    console.print(f"[bold]Reps/level:[/bold]   {cfg.load.requests_per_level}")
    console.print(f"[bold]Warmup:[/bold]       {cfg.load.warmup_requests}")
    console.print(f"[bold]Stream:[/bold]       {cfg.load.stream}")
    console.print(f"[bold]Output:[/bold]       {cfg.export.output_dir} ({cfg.export.formats})")
    if args.dry_run:
        console.print("\n[cyan](dry-run: config valid, no requests sent)[/cyan]")
        return 0

    try:
        if multi_model:
            suite = asyncio.run(Ramp(cfg, console=console).run_suite())
            files = write_suite_reports(suite, cfg.export.formats, cfg.export.output_dir)
        else:
            mc_result = asyncio.run(Ramp(cfg, console=console).run())
            files = write_reports(mc_result, cfg.export.formats, cfg.export.output_dir)
    except KeyboardInterrupt:
        console.print("[yellow]interrupted[/yellow]")
        return 130

    console.rule("[bold green]done")
    for f in files:
        console.print(f"wrote {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
