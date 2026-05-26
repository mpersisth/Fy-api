"""CLI entrypoint for cache affinity benchmark."""

from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console

from .config import Config
from .report import write_reports
from .runner import run_benchmark


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fy-cache-affinity",
        description="Multi-turn conversation cache hit rate benchmark for Fy-api channel affinity.",
    )
    p.add_argument("command", choices=["run"], help="Command to execute")
    p.add_argument("config", help="Path to YAML config file")
    p.add_argument("--group", help="Run only the named group")
    p.add_argument("--dry-run", action="store_true", help="Validate config and exit")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()

    try:
        cfg = Config.load(args.config)
        cfg.validate()
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]config: {e}[/red]")
        return 2

    console.print(f"[bold]Gateway:[/bold]      {cfg.base_url}")
    console.print(f"[bold]Model:[/bold]        {cfg.model}")
    console.print(f"[bold]Groups:[/bold]       {[g.name for g in cfg.groups]}")
    console.print(f"[bold]Repetitions:[/bold]  {cfg.repetitions}")
    console.print(f"[bold]Max turns:[/bold]    {cfg.conversation.max_turns}")
    console.print(f"[bold]Max tokens:[/bold]   {cfg.conversation.max_prompt_tokens}")
    console.print(f"[bold]Output:[/bold]       {cfg.export.output_dir} ({cfg.export.formats})")

    if args.dry_run:
        console.print("\n[cyan](dry-run: config valid, no requests sent)[/cyan]")
        return 0

    try:
        result = asyncio.run(run_benchmark(cfg, group_filter=args.group, console=console))
        files = write_reports(result, cfg.export.formats, cfg.export.output_dir)
    except KeyboardInterrupt:
        console.print("[yellow]interrupted[/yellow]")
        return 130

    console.rule("[bold green]done")
    for f in files:
        console.print(f"wrote {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
