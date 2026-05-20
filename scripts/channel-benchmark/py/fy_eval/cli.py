"""CLI entry point for fy_eval."""

from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console

from .config import Config
from .orchestrator import run_eval
from .report import generate_markdown, save_report

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fy-eval",
        description="Unified channel evaluation framework",
    )
    parser.add_argument("config", help="Path to YAML config file")
    parser.add_argument("--stdout", action="store_true",
                       help="Print report to stdout instead of saving to file")
    parser.add_argument("--skip-load", action="store_true",
                       help="Skip load tests (faster evaluation)")
    parser.add_argument("--skip-safety", action="store_true",
                       help="Skip safety tests")
    args = parser.parse_args()

    cfg = Config.load(args.config)

    # Apply CLI overrides
    if args.skip_load:
        if cfg.text_models:
            cfg.text_models.tests.load = {}
        if cfg.image_models:
            cfg.image_models.tests.load = {}
        if cfg.video_models:
            cfg.video_models.tests.load = {}
    if args.skip_safety:
        if cfg.image_models:
            cfg.image_models.tests.safety = False

    console.print(f"[bold]Channel Evaluation: {cfg.channel.name}[/bold]")
    console.print(f"  base_url: {cfg.channel.base_url}")

    result = asyncio.run(run_eval(cfg, console))

    console.print(f"\n[bold]Overall: {result.overall_verdict.value}[/bold]")

    if args.stdout:
        print("\n" + generate_markdown(cfg, result))
    else:
        filepath = save_report(cfg, result)
        console.print(f"[bold green]Report saved:[/bold green] {filepath}")
