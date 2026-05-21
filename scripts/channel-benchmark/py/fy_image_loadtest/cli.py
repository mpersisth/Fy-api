"""CLI entrypoint for fy-image-loadtest."""

from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console

from . import __version__
from .config import Config
from .report import write_reports
from .runner import ImageRamp


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fy-image-loadtest",
        description="Image-generation load tester for Fy-api pinned channels.",
    )
    p.add_argument("-c", "--config", default="image-loadtest.yaml", help="Path to YAML config")
    p.add_argument("--base-url", help="Override gateway.base_url")
    p.add_argument("--user-token", help="Override gateway.user_token")
    p.add_argument("--model", help="Override image.model")
    p.add_argument("--prompt", help="Override image.prompt")
    p.add_argument("--concurrency", type=int, help="Override image.concurrency_per_channel")
    p.add_argument("--timeout", type=float, help="Override image.request_timeout_sec")
    p.add_argument("--duration-sec", type=float, help="Override image.duration_sec")
    p.add_argument("--max-requests", type=int, help="Override image.max_requests_per_channel")
    p.add_argument("--output", help="Override export.output_dir")
    p.add_argument(
        "--formats",
        help="Override export.formats (comma-separated: json,csv,markdown)",
    )
    p.add_argument("--dry-run", action="store_true", help="Validate config and exit")
    p.add_argument("-V", "--version", action="version", version=f"fy-image-loadtest {__version__}")
    return p


def apply_overrides(cfg: Config, args: argparse.Namespace) -> None:
    if args.base_url:
        cfg.gateway.base_url = args.base_url
    if args.user_token:
        cfg.gateway.user_token = args.user_token
    if args.model:
        cfg.image.model = args.model
    if args.prompt:
        cfg.image.prompt = args.prompt
    if args.concurrency:
        cfg.image.concurrency_per_channel = args.concurrency
    if args.timeout:
        cfg.image.request_timeout_sec = args.timeout
    if args.duration_sec is not None:
        cfg.image.duration_sec = args.duration_sec
        cfg.image.continuous = False
    if args.max_requests is not None:
        cfg.image.max_requests_per_channel = args.max_requests
        cfg.image.continuous = False
    if args.output:
        cfg.export.output_dir = args.output
    if args.formats:
        cfg.export.formats = [x.strip() for x in args.formats.split(",") if x.strip()]


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

    console.print(f"[bold]Gateway:[/bold]      {cfg.gateway.base_url}")
    console.print(f"[bold]Model:[/bold]        {cfg.image.model}")
    console.print(f"[bold]Channels:[/bold]     {', '.join(f'{c.name}(id={c.pin_channel_id})' for c in cfg.gateway.channels)}")
    console.print(f"[bold]Concurrency:[/bold]  {cfg.image.concurrency_per_channel} per channel")
    console.print(
        f"[bold]Image:[/bold]        size={cfg.image.size} quality={cfg.image.quality} "
        f"n={cfg.image.n} format={cfg.image.response_format or '<unset>'}"
    )
    console.print(f"[bold]Timeout:[/bold]      {cfg.image.request_timeout_sec}s")
    console.print(f"[bold]Continuous:[/bold]   {cfg.image.continuous}")
    if cfg.image.duration_sec is not None:
        console.print(f"[bold]Duration:[/bold]     {cfg.image.duration_sec}s")
    if cfg.image.max_requests_per_channel is not None:
        console.print(f"[bold]Max/channel:[/bold]  {cfg.image.max_requests_per_channel}")
    console.print(f"[bold]Output:[/bold]       {cfg.export.output_dir} ({cfg.export.formats})")
    if args.dry_run:
        console.print("\n[cyan](dry-run: config valid, no requests sent)[/cyan]")
        return 0

    try:
        result = asyncio.run(ImageRamp(cfg, console=console).run())
    except KeyboardInterrupt:
        console.print("[yellow]interrupted[/yellow]")
        return 130

    files = write_reports(result, cfg.export.formats, cfg.export.output_dir)
    console.rule("[bold green]done")
    for f in files:
        console.print(f"wrote {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
