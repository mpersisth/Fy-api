"""CLI entry point for fy_image_conformance."""

from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console

from .config import Config
from .client import ImageClient
from .probe import probe_channel
from .report import FullReport, generate_markdown, save_report
from .suites import api_compat, output_valid, prompt_follow, perf, safety

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fy_image_conformance",
        description="Unified image channel conformance testing",
    )
    parser.add_argument("config", help="Path to YAML config file")
    parser.add_argument("--probe-only", action="store_true",
                       help="Only probe which models the channel supports")
    parser.add_argument("--skip-perf", action="store_true",
                       help="Skip performance load test")
    parser.add_argument("--skip-safety", action="store_true",
                       help="Skip safety & boundary tests")
    parser.add_argument("--skip-prompt", action="store_true",
                       help="Skip prompt adherence tests")
    parser.add_argument("--stdout", action="store_true",
                       help="Print report to stdout instead of file")
    args = parser.parse_args()

    cfg = Config.load(args.config)
    asyncio.run(_run(cfg, args))


async def _run(cfg: Config, args: argparse.Namespace) -> None:
    report = FullReport(config=cfg)

    async with ImageClient(
        cfg.gateway.base_url, cfg.gateway.user_token,
        timeout=cfg.suites.perf.request_timeout_sec,
    ) as client:

        # Probe mode: just detect supported models
        if args.probe_only:
            console.print("[bold]Probing supported image models...[/bold]")
            for ch in cfg.gateway.channels:
                console.print(f"  Channel: {ch.name} (ID:{ch.pin_channel_id})")
                probes = await probe_channel(client, ch)
                report.probe_results[ch.name] = probes
                supported = [p for p in probes if p.supported]
                console.print(f"    Supported: {len(supported)}/{len(probes)}")
                for p in supported:
                    console.print(f"      [green]{p.model}[/green]")
        else:
            # Layer 1: API compatibility
            if cfg.suites.api_compat:
                console.print("[bold]Layer 1: API compatibility...[/bold]")
                report.compat_results = await api_compat.run(cfg, client)
                for cr in report.compat_results:
                    console.print(f"  {cr.channel.name}: {cr.passed}/{len(cr.cases)} passed")

            # Layer 2: Output validation
            if cfg.suites.output_valid:
                console.print("[bold]Layer 2: Output validation...[/bold]")
                report.output_results = await output_valid.run(cfg, client)
                for cr in report.output_results:
                    console.print(f"  {cr.channel.name}: {cr.passed}/{len(cr.cases)} passed")

            # Layer 3: Prompt adherence
            if cfg.suites.prompt_follow.enabled and not args.skip_prompt:
                console.print("[bold]Layer 3: Prompt adherence (VLM judge)...[/bold]")
                report.prompt_results = await prompt_follow.run(cfg, client)
                for cr in report.prompt_results:
                    console.print(f"  {cr.channel.name}: avg score {cr.avg_score:.2f}")

            # Layer 4: Performance
            if cfg.suites.perf.enabled and not args.skip_perf:
                console.print("[bold]Layer 4: Performance load test...[/bold]")
                report.perf_results = await perf.run(cfg, client)
                for ps in report.perf_results:
                    console.print(
                        f"  {ps.channel.name}: {ps.total_requests} reqs, "
                        f"{ps.success_rate:.0%} success, P95={ps.p95_ms/1000:.1f}s"
                    )

            # Layer 5: Safety
            if cfg.suites.safety and not args.skip_safety:
                console.print("[bold]Layer 5: Safety & boundary...[/bold]")
                report.safety_results = await safety.run(cfg, client)
                for cr in report.safety_results:
                    console.print(f"  {cr.channel.name}: {cr.passed}/{len(cr.cases)} passed")

    # Generate report
    if args.stdout:
        console.print("\n")
        print(generate_markdown(report))
    else:
        filepath = save_report(report, cfg.export.output_dir)
        console.print(f"\n[bold green]Report saved:[/bold green] {filepath}")
