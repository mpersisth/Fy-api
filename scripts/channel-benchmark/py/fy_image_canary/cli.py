"""CLI entry point for fy-image-canary."""

from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console

from . import __version__
from .config import ImageCanaryConfig
from .runner import ImageCanaryRunner
from .report import generate_markdown, save_report

console = Console()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fy-image-canary",
        description="Image model-substitution detection for Fy-api channels.",
    )
    p.add_argument("-c", "--config", default="image-canary.yaml",
                  help="Path to YAML config file")
    p.add_argument("--vendor-only", action="store_true",
                  help="Run only 5A vendor comparison")
    p.add_argument("--fingerprint-only", action="store_true",
                  help="Run only 5B-1 fingerprint probes")
    p.add_argument("--calibrate", action="store_true",
                  help="Run threshold calibration (generate N images per prompt, compute distributions)")
    p.add_argument("--calibrate-n", type=int, default=10,
                  help="Number of generations per prompt for calibration (default: 10)")
    p.add_argument("--dry-run", action="store_true",
                  help="Validate config and exit")
    p.add_argument("--stdout", action="store_true",
                  help="Print report to stdout")
    p.add_argument("-V", "--version", action="version",
                  version=f"fy-image-canary {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        cfg = ImageCanaryConfig.load(args.config)
    except Exception as e:
        console.print(f"[red]Config error: {e}[/red]")
        return 1

    if args.dry_run:
        console.print("[green]Config validated successfully.[/green]")
        console.print(f"  Gateway: {cfg.gateway.name} ({cfg.gateway.model})")
        console.print(f"  Vendor: {'configured' if cfg.vendor else 'not configured (5A skipped)'}")
        console.print(f"  Additional channels: {len(cfg.additional_channels)}")
        console.print(f"  Test prompts: {len(cfg.test_prompts)}")
        return 0

    if args.calibrate:
        return _run_calibration(cfg, args)

    runner = ImageCanaryRunner(cfg)
    try:
        if args.vendor_only:
            report = asyncio.run(runner.run_vendor_only())
        elif args.fingerprint_only:
            report = asyncio.run(runner.run_fingerprint_only())
        else:
            report = asyncio.run(runner.run_full())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        return 130

    if args.stdout:
        print(generate_markdown(report))
    else:
        json_path, md_path = save_report(report, cfg.output_dir)
        console.print(f"[green]JSON:[/green] {json_path}")
        console.print(f"[green]Markdown:[/green] {md_path}")

    passed = sum(1 for o in report.outcomes if o.passed)
    total = len(report.outcomes)
    console.print(
        f"\nVerdict: [bold]{report.combined_verdict}[/bold] "
        f"({report.combined_confidence} confidence) "
        f"| {passed}/{total} probes passed"
    )
    return 0 if report.combined_verdict == "PASS" else 1


def _run_calibration(cfg: ImageCanaryConfig, args: argparse.Namespace) -> int:
    from .calibrate import run_calibration, save_calibration

    console.print(f"[bold]Calibrating thresholds[/bold] — "
                 f"{len(cfg.test_prompts)} prompts × {args.calibrate_n} generations each")
    console.print(f"  Channel: {cfg.gateway.name} ({cfg.gateway.model})")
    console.print(f"  Estimated cost: ~${len(cfg.test_prompts) * args.calibrate_n * cfg.budget.cost_per_generation:.2f}")
    console.print("")

    try:
        report = asyncio.run(run_calibration(cfg, n_per_prompt=args.calibrate_n))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        return 130

    filepath = save_calibration(report, cfg.output_dir)
    console.print(f"[green]Calibration saved:[/green] {filepath}")
    console.print("")
    console.print("[bold]Recommended thresholds:[/bold]")
    console.print(f"  CLIP cosine:       {report.recommended_clip_threshold:.4f}")
    console.print(f"  Color correlation: {report.recommended_color_threshold:.4f}")
    console.print("")

    for r in report.results:
        clip_info = f"CLIP μ={r.clip_mean:.4f} σ={r.clip_std:.4f}" if r.clip_cosines else "CLIP: N/A"
        color_info = f"Color μ={r.color_mean:.4f} σ={r.color_std:.4f}" if r.color_correlations else "Color: N/A"
        console.print(f"  {r.prompt[:50]:50s} | {r.n_pairs:3d} pairs | {clip_info} | {color_info}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
