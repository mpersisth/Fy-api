"""CLI entry point for fy_image_conformance."""

from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console
from rich.table import Table

from .config import Config
from .client import ImageClient
from .probe import probe_channel
from .budget import BudgetTracker, estimate_steps
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
    parser.add_argument("--dry-run", action="store_true",
                       help="Validate config, print cost estimate, exit")
    parser.add_argument("--max-cost", type=float, default=None,
                       help="Hard budget limit in USD; abort when exceeded")
    parser.add_argument("--smoke-only", action="store_true",
                       help="Run Layer 1+2 only (cheapest mode)")
    parser.add_argument("--phase-a-only", action="store_true",
                       help="Run through Layer 3 Phase A only")
    args = parser.parse_args()

    cfg = Config.load(args.config)

    if args.dry_run:
        _print_cost_estimate(cfg, smoke_only=args.smoke_only, phase_a_only=args.phase_a_only)
        return

    asyncio.run(_run(cfg, args))


def _print_cost_estimate(cfg: Config, *, smoke_only: bool, phase_a_only: bool) -> None:
    estimates = estimate_steps(cfg, smoke_only=smoke_only, phase_a_only=phase_a_only)
    table = Table(title="预估费用")
    table.add_column("步骤", style="cyan")
    table.add_column("请求数", justify="right")
    table.add_column("预估费用 (USD)", justify="right", style="yellow")
    table.add_column("说明")
    total_requests = 0
    total_cost = 0.0
    for est in estimates:
        table.add_row(est.step_name, str(est.estimated_requests),
                     f"${est.estimated_cost_usd:.3f}", est.detail)
        total_requests += est.estimated_requests
        total_cost += est.estimated_cost_usd
    table.add_row("[bold]合计[/bold]", f"[bold]{total_requests}[/bold]",
                 f"[bold]${total_cost:.3f}[/bold]", "")
    console.print(table)
    if cfg.budget.max_cost_usd is not None:
        console.print(f"预算上限: ${cfg.budget.max_cost_usd:.2f}")


def _make_tracker(cfg: Config, args: argparse.Namespace) -> BudgetTracker:
    max_cost = args.max_cost if args.max_cost is not None else cfg.budget.max_cost_usd
    return BudgetTracker(
        max_cost_usd=max_cost,
        warn_cost_usd=cfg.budget.warn_cost_usd,
        default_cost=cfg.budget.default_cost_per_request,
    )


async def _run(cfg: Config, args: argparse.Namespace) -> None:
    report = FullReport(config=cfg)
    tracker = _make_tracker(cfg, args)
    cost = cfg.budget.default_cost_per_request

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
            n_ch = len(cfg.gateway.channels)

            # Layer 1: API compatibility
            if cfg.suites.api_compat:
                console.print("[bold]Layer 1: API compatibility...[/bold]")
                report.compat_results = await api_compat.run(cfg, client)
                n_cases = sum(len(cr.cases) for cr in report.compat_results)
                tracker.record("API兼容性", n_cases, cost)
                for cr in report.compat_results:
                    console.print(f"  {cr.channel.name}: {cr.passed}/{len(cr.cases)} passed")

            # Layer 2: Output validation
            if cfg.suites.output_valid:
                console.print("[bold]Layer 2: Output validation...[/bold]")
                report.output_results = await output_valid.run(cfg, client)
                tracker.record("输出验证", n_ch, cost)
                for cr in report.output_results:
                    console.print(f"  {cr.channel.name}: {cr.passed}/{len(cr.cases)} passed")

            if args.smoke_only:
                console.print("[yellow]--smoke-only: stopping after Layer 2[/yellow]")
                report.budget_summary = tracker.summary()
                _save_or_print(report, cfg, args)
                return

            # Layer 3: Prompt adherence
            if cfg.suites.prompt_follow.enabled and not args.skip_prompt:
                pf = cfg.suites.prompt_follow
                est_cost = pf.sample_count * n_ch * (cost + 0.005 * pf.judge_repeat)
                if tracker.would_exceed(est_cost):
                    console.print(f"[red]Budget exceeded, skipping Layer 3 (est ${est_cost:.3f})[/red]")
                else:
                    console.print("[bold]Layer 3: Prompt adherence (VLM judge)...[/bold]")
                    report.prompt_results = await prompt_follow.run(cfg, client)
                    tracker.record("质量评测", pf.sample_count * n_ch, cost)
                    for cr in report.prompt_results:
                        console.print(f"  {cr.channel.name}: avg score {cr.avg_score:.2f}")

            if args.phase_a_only:
                console.print("[yellow]--phase-a-only: stopping after Layer 3[/yellow]")
                report.budget_summary = tracker.summary()
                _save_or_print(report, cfg, args)
                return

            # Budget warning check
            if tracker.should_warn():
                console.print(f"[yellow]Warning: spent ${tracker.total_spent:.3f} "
                             f"(warn threshold: ${tracker.warn_cost_usd})[/yellow]")

            # Layer 4: Performance
            if cfg.suites.perf.enabled and not args.skip_perf:
                console.print("[bold]Layer 4: Performance load test...[/bold]")
                report.perf_results = await perf.run(cfg, client)
                n_perf = sum(ps.total_requests for ps in report.perf_results)
                tracker.record("性能测试", n_perf, cost)
                for ps in report.perf_results:
                    console.print(
                        f"  {ps.channel.name}: {ps.total_requests} reqs, "
                        f"{ps.success_rate:.0%} success, P95={ps.p95_ms/1000:.1f}s"
                    )

            # Layer 5: Safety
            if cfg.suites.safety and not args.skip_safety:
                console.print("[bold]Layer 5: Safety & boundary...[/bold]")
                report.safety_results = await safety.run(cfg, client)
                n_safety = sum(len(cr.cases) for cr in report.safety_results)
                tracker.record("安全测试", n_safety, cost)
                for cr in report.safety_results:
                    console.print(f"  {cr.channel.name}: {cr.passed}/{len(cr.cases)} passed")

    report.budget_summary = tracker.summary()
    _save_or_print(report, cfg, args)


def _save_or_print(report: FullReport, cfg: Config, args: argparse.Namespace) -> None:
    if args.stdout:
        console.print("\n")
        print(generate_markdown(report))
    else:
        filepath = save_report(report, cfg.export.output_dir)
        console.print(f"\n[bold green]Report saved:[/bold green] {filepath}")
        # JSON is saved alongside with same base name
        json_path = filepath.replace(".md", ".json")
        console.print(f"[bold green]JSON summary:[/bold green] {json_path}")
