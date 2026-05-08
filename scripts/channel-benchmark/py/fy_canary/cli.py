"""fy-canary CLI with `baseline` and `audit` subcommands."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from . import __version__
from .config import CanaryConfig
from .runner import CanaryReport, CanaryRunner, report_to_dict


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fy-canary",
        description="Model-substitution detection for Fy-api channels.",
    )
    p.add_argument("-V", "--version", action="version", version=f"fy-canary {__version__}")

    sub = p.add_subparsers(dest="cmd", required=True)

    p_base = sub.add_parser("baseline", help="Build a trusted baseline for the source")
    p_base.add_argument("-c", "--config", default="canary.yaml")
    p_base.add_argument("--dry-run", action="store_true")

    p_audit = sub.add_parser("audit", help="Compare the current source to its baseline")
    p_audit.add_argument("-c", "--config", default="canary.yaml")
    p_audit.add_argument("--output", help="Override output directory")
    p_audit.add_argument("--dry-run", action="store_true")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()

    try:
        cfg = CanaryConfig.load(args.config)
        cfg.validate()
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]config: {e}[/red]")
        return 2

    console.print(f"[bold]Mode:[/]        {args.cmd}")
    console.print(f"[bold]Source:[/]      {cfg.source.name} ({cfg.source.model})")
    console.print(f"[bold]Base URL:[/]    {cfg.source.base_url}")
    console.print(f"[bold]Dataset:[/]     {cfg.dataset}")
    console.print(f"[bold]Baselines:[/]   {cfg.baselines_dir}")
    console.print(f"[bold]MMD:[/]         {'enabled' if cfg.mmd_enabled else 'disabled'}")
    console.print(f"[bold]Embedding:[/]   {'configured' if cfg.embedding else 'disabled'}")

    if args.dry_run:
        console.print("\n[cyan](dry-run: config valid, no requests sent)[/cyan]")
        return 0

    runner = CanaryRunner(cfg)
    try:
        if args.cmd == "baseline":
            baseline = asyncio.run(runner.build_baseline())
            path = runner.store.path_for(baseline.source_name)
            console.rule("[bold green]baseline saved")
            console.print(f"{len(baseline.probes)} probes saved to {path}")
            return 0

        report: CanaryReport = asyncio.run(runner.audit())
    except (KeyboardInterrupt, FileNotFoundError) as e:
        console.print(f"[red]{e}[/red]")
        return 2 if isinstance(e, FileNotFoundError) else 130

    out_dir = Path(getattr(args, "output", None) or cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    json_path = out_dir / f"canary_{ts}.json"
    md_path = out_dir / f"canary_{ts}.md"

    json_path.write_text(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
    md_path.write_text(_markdown(report))

    console.rule("[bold green]audit done")
    total = len(report.outcomes)
    failed = [o for o in report.outcomes if not o.passed]
    if failed:
        console.print(f"[red]{len(failed)}/{total} probes failed[/red]")
        for o in failed:
            console.print(f"  - {o.prompt_id} ({o.method}): {o.detail}")
    else:
        console.print(f"[green]all {total} probes passed[/green]")
    console.print(f"wrote {json_path}")
    console.print(f"wrote {md_path}")
    return 0 if not failed else 1


def _markdown(report: CanaryReport) -> str:
    lines: list[str] = []
    lines.append("# Canary audit")
    lines.append("")
    lines.append(f"- Source: `{report.source_name}` (model `{report.model}`)")
    lines.append(f"- Generated: {datetime.fromtimestamp(report.generated_at_unix, timezone.utc).isoformat()}")
    lines.append("")
    lines.append("| Prompt | Method | Passed | Score | Detail |")
    lines.append("|---|---|---:|---:|---|")
    for o in report.outcomes:
        detail = o.detail.replace("|", "\\|")
        if len(detail) > 120:
            detail = detail[:117] + "..."
        lines.append(
            f"| `{o.prompt_id}` | {o.method} | {'✓' if o.passed else '✗'} | "
            f"{o.score:.3f} | {detail} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
