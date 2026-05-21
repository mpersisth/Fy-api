"""fy-canary CLI with `baseline`, `audit`, and `verify-baseline` subcommands."""

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
    p_audit.add_argument(
        "--ignore-stale-baseline",
        action="store_true",
        help="Proceed even if the loaded baseline is older than baseline_max_age_days",
    )

    p_verify = sub.add_parser(
        "verify-baseline",
        help="Re-record a fresh mini-baseline against the same source and "
             "compare to the stored one (catches baseline drift)",
    )
    p_verify.add_argument("-c", "--config", default="canary.yaml")
    p_verify.add_argument("--output", help="Override output directory")
    p_verify.add_argument("--dry-run", action="store_true")

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

    # Health-check the baseline up-front for the two modes that need it.
    if args.cmd in {"audit", "verify-baseline"}:
        health = runner.baseline_health()
        if health is None:
            console.print(
                f"[red]no baseline found for {cfg.source.name!r} — "
                f"run `fy-canary baseline -c {args.config}` first[/red]"
            )
            return 2
        _print_health(console, health)
        if health["stale"]:
            if args.cmd == "audit" and not getattr(args, "ignore_stale_baseline", False):
                console.print(
                    f"[red]baseline is stale ({health['age_days']:.0f} days > "
                    f"{health['max_age_days']} days). "
                    f"Re-record with `fy-canary baseline` or pass "
                    f"`--ignore-stale-baseline` to override.[/red]"
                )
                return 2
            console.print(
                f"[yellow]warning: baseline is stale "
                f"({health['age_days']:.0f} days old).[/yellow]"
            )

    try:
        if args.cmd == "baseline":
            baseline = asyncio.run(runner.build_baseline())
            path = runner.store.path_for(baseline.source_name)
            console.rule("[bold green]baseline saved")
            console.print(
                f"{baseline.n_probes} probes, "
                f"{baseline.total_samples} total samples saved to {path}"
            )
            return 0

        if args.cmd == "verify-baseline":
            report: CanaryReport = asyncio.run(runner.verify_baseline())
        else:
            report = asyncio.run(runner.audit())
    except (KeyboardInterrupt, FileNotFoundError) as e:
        console.print(f"[red]{e}[/red]")
        return 2 if isinstance(e, FileNotFoundError) else 130

    out_dir = Path(getattr(args, "output", None) or cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    prefix = "verify" if args.cmd == "verify-baseline" else "canary"
    json_path = out_dir / f"{prefix}_{ts}.json"
    md_path = out_dir / f"{prefix}_{ts}.md"

    json_path.write_text(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")

    rule_label = (
        "verify-baseline done" if args.cmd == "verify-baseline" else "audit done"
    )
    console.rule(f"[bold green]{rule_label}")
    total = len(report.outcomes)
    failed = [o for o in report.outcomes if not o.passed]
    if failed:
        if args.cmd == "verify-baseline":
            console.print(
                f"[yellow]{len(failed)}/{total} probes diverged from the recorded baseline[/yellow]\n"
                f"[yellow]→ the SOURCE itself may have changed. Consider re-recording the baseline.[/yellow]"
            )
        else:
            console.print(f"[red]{len(failed)}/{total} probes failed[/red]")
        for o in failed:
            console.print(f"  - {o.prompt_id} ({o.method}): {o.detail}")
    else:
        console.print(f"[green]all {total} probes passed[/green]")
    console.print(f"wrote {json_path}")
    console.print(f"wrote {md_path}")
    return 0 if not failed else 1


def _print_health(console: Console, health: dict) -> None:
    age = health["age_days"]
    color = "yellow" if health["stale"] else "green"
    console.print(
        f"[bold]Baseline:[/]    [{color}]{age:.1f} days old, "
        f"{health['n_probes']} probes, {health['total_samples']} samples[/{color}] "
        f"(recorded {health['recorded_at_iso']})"
    )


def _markdown(report: CanaryReport) -> str:
    lines: list[str] = []
    title = "Canary verify-baseline" if report.mode == "verify-baseline" else "Canary audit"
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- Mode: `{report.mode}`")
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
