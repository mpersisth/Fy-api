"""CLI entry point for fy-integrity."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.rule import Rule

from . import __version__
from .config import IntegrityConfig
from .report import write_reports
from .runner import IntegrityRunner


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fy-integrity",
        description="Channel integrity/honesty auditing for Fy-api channels.",
    )
    p.add_argument(
        "-V", "--version", action="version", version=f"fy-integrity {__version__}"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Execute integrity probes")
    p_run.add_argument("-c", "--config", default="integrity.yaml")
    p_run.add_argument("--probe", help="Run only this probe (by name)")
    p_run.add_argument("--dry-run", action="store_true")

    p_report = sub.add_parser("report", help="Re-generate report from JSON")
    p_report.add_argument("json_file", help="Path to previous result JSON")
    p_report.add_argument(
        "--format", choices=["markdown"], default="markdown"
    )
    p_report.add_argument("-o", "--output-dir", default="integrity-results")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()

    try:
        if args.cmd == "run":
            return _cmd_run(args, console)
        elif args.cmd == "report":
            return _cmd_report(args, console)
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/dim]")
        return 130
    except Exception as e:
        console.print(f"[red]error:[/red] {e}")
        return 2
    return 0


def _cmd_run(args, console: Console) -> int:
    config = IntegrityConfig.load(args.config)

    console.print(Rule("fy-integrity"))
    console.print(f"  model:   {config.target.model}")
    if config.gateway.pin_channel_id:
        console.print(f"  channel: {config.gateway.pin_channel_id}")
    console.print()

    if args.dry_run:
        runner = IntegrityRunner(config, console=console)
        probes = runner._enabled_probes(args.probe)
        console.print("[dim]dry-run: would execute these probes:[/dim]")
        for p in probes:
            console.print(f"  - {p.name} (severity={p.severity})")
        return 0

    runner = IntegrityRunner(config, console=console)
    results = asyncio.run(runner.run_all(probe_filter=args.probe))

    console.print()
    config_summary = {
        "model": config.target.model,
        "pin_channel_id": config.gateway.pin_channel_id,
        "base_url": config.gateway.base_url,
    }
    written = write_reports(
        results,
        config_summary=config_summary,
        formats=config.export.formats,
        output_dir=config.export.output_dir,
    )
    for p in written:
        console.print(f"  report: {p}")

    failed = [r for r in results if not r.passed]
    if failed:
        console.print(
            f"\n[red bold]FAIL[/red bold] — {len(failed)} probe(s) failed"
        )
        return 1

    console.print("\n[green bold]PASS[/green bold] — all probes passed")
    return 0


def _cmd_report(args, console: Console) -> int:
    data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    from .report import _render_markdown

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / (Path(args.json_file).stem + ".md")
    md_path.write_text(_render_markdown(data), encoding="utf-8")
    console.print(f"  report: {md_path}")
    return 0
