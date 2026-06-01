"""CLI entrypoint for fy-score (v0.2)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from rich.console import Console

from . import __version__
from .loader import (
    load_canary, load_conformance, load_integrity, load_loadtest,
    load_quality, load_smoke, load_image_canary,
    load_image_conformance, load_image_loadtest,
)
from .report import write_json, write_markdown
from .scorer import ChannelScorecard, build_scorecard, build_image_scorecard, compute_integrity_rates


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fy-score",
        description="SLO-anchored channel scorecard generator (5 dimensions).",
    )
    p.add_argument("-c", "--config", type=Path, help="YAML config file")
    p.add_argument("--smoke", type=Path, nargs="*", help="Go smoke-test result JSON(s)")
    p.add_argument("--loadtest", type=Path, nargs="*", help="fy-loadtest result JSON(s)")
    p.add_argument("--quality", type=Path, nargs="*", help="fy-quality result JSON(s)")
    p.add_argument("--canary", type=Path, nargs="*", help="fy-canary result JSON(s)")
    p.add_argument("--conformance", type=Path, nargs="*", help="fy-conformance summary JSON(s)")
    p.add_argument("--integrity", type=Path, nargs="*", help="fy-integrity result JSON(s)")
    p.add_argument("--smoke-dir", type=Path)
    p.add_argument("--loadtest-dir", type=Path)
    p.add_argument("--quality-dir", type=Path)
    p.add_argument("--canary-dir", type=Path)
    p.add_argument("--conformance-dir", type=Path)
    p.add_argument("--integrity-dir", type=Path)
    p.add_argument("-o", "--output", type=Path, default=Path("scorecard.json"))
    p.add_argument("--markdown", type=Path, help="Also write Markdown scorecard")
    p.add_argument("--channel-id", type=int)
    p.add_argument("--channel-name")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-V", "--version", action="version", version=f"fy-score {__version__}")
    p.add_argument("--image-canary", type=Path, nargs="*", help="fy-image-canary result JSON(s)")
    p.add_argument("--image-canary-dir", type=Path)
    p.add_argument("--image-conformance", type=Path, nargs="*", help="fy-image-conformance result JSON(s)")
    p.add_argument("--image-conformance-dir", type=Path)
    p.add_argument("--image-loadtest", type=Path, nargs="*", help="fy-image-loadtest result JSON(s)")
    p.add_argument("--image-loadtest-dir", type=Path)
    return p
# PLACEHOLDER_CLI_CONTINUE


def _collect_files(explicit: list[Path] | None, directory: Path | None, suffix: str = ".json") -> list[Path]:
    files: list[Path] = []
    if explicit:
        files.extend(explicit)
    if directory and directory.is_dir():
        files.extend(sorted(directory.glob(f"*{suffix}")))
    return files


def _merge(inputs: dict[str, dict], channel_name: str, channel_id: int | None, model: str) -> str:
    k_id = f"chid:{channel_id}||{model}" if channel_id is not None else None
    k_name = f"name:{channel_name}||{model}"
    if k_id and k_id in inputs:
        return k_id
    if k_name in inputs:
        return k_name
    k = k_id if k_id else k_name
    inputs[k] = {"channel_name": channel_name, "channel_id": channel_id, "model": model}
    return k


def _apply_yaml_config(args: argparse.Namespace) -> None:
    """Overlay YAML config onto args (CLI flags take priority)."""
    if not args.config or not args.config.exists():
        return
    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    if args.channel_id is None and "channel_id" in cfg:
        args.channel_id = cfg["channel_id"]
    if args.channel_name is None and "channel_name" in cfg:
        args.channel_name = cfg["channel_name"]

    inputs = cfg.get("inputs", {})
    for tool in ("smoke", "loadtest", "quality", "canary", "conformance", "integrity",
                  "image_canary", "image_conformance", "image_loadtest"):
        dir_attr = f"{tool}_dir"
        if getattr(args, dir_attr, None) is None and tool in inputs:
            setattr(args, dir_attr, Path(inputs[tool]))

    output = cfg.get("output", {})
    if output.get("json"):
        args.output = Path(output["json"])
    if output.get("markdown") and args.markdown is None:
        args.markdown = Path(output["markdown"])


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _apply_yaml_config(args)
    console = Console()

    smoke_files = _collect_files(args.smoke, args.smoke_dir)
    lt_files = _collect_files(args.loadtest, args.loadtest_dir)
    qa_files = _collect_files(args.quality, args.quality_dir)
    canary_files = _collect_files(args.canary, args.canary_dir)
    conf_files = _collect_files(args.conformance, args.conformance_dir)
    integ_files = _collect_files(args.integrity, args.integrity_dir)
    img_canary_files = _collect_files(
        getattr(args, "image_canary", None),
        getattr(args, "image_canary_dir", None),
    )
    img_conf_files = _collect_files(
        getattr(args, "image_conformance", None),
        getattr(args, "image_conformance_dir", None),
    )
    img_lt_files = _collect_files(
        getattr(args, "image_loadtest", None),
        getattr(args, "image_loadtest_dir", None),
    )
# PLACEHOLDER_CLI_MAIN

    if args.dry_run:
        console.print(f"[bold]Smoke:[/bold]       {[str(f) for f in smoke_files]}")
        console.print(f"[bold]Loadtest:[/bold]    {[str(f) for f in lt_files]}")
        console.print(f"[bold]Quality:[/bold]     {[str(f) for f in qa_files]}")
        console.print(f"[bold]Canary:[/bold]      {[str(f) for f in canary_files]}")
        console.print(f"[bold]Conformance:[/bold] {[str(f) for f in conf_files]}")
        console.print(f"[bold]Integrity:[/bold]   {[str(f) for f in integ_files]}")
        console.print(f"[bold]ImgCanary:[/bold]   {[str(f) for f in img_canary_files]}")
        console.print(f"[bold]ImgConf:[/bold]     {[str(f) for f in img_conf_files]}")
        console.print(f"[bold]ImgLoad:[/bold]     {[str(f) for f in img_lt_files]}")
        return 0

    inputs: dict[str, dict] = {}

    for f in smoke_files:
        for m in load_smoke(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["success_rate"] = m.success_rate

    for f in lt_files:
        for m in load_loadtest(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["ttft_p95_ms"] = m.ttft_p95_ms
            inputs[k]["e2e_p95_ms"] = m.e2e_p95_ms
            inputs[k]["throughput_toks"] = m.throughput_toks
            if "success_rate" not in inputs[k]:
                levels = []
                for ch in _read_loadtest_raw(f):
                    levels.extend(ch.get("levels", []))
                if levels:
                    total_ok = sum(lv.get("ok", 0) for lv in levels)
                    total_req = sum(lv.get("total", 0) for lv in levels)
                    if total_req > 0:
                        inputs[k]["success_rate"] = total_ok / total_req

    for f in qa_files:
        for m in load_quality(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["quality_pass_rate"] = m.pass_rate
            inputs[k]["quality_avg_score"] = m.avg_score

    for f in canary_files:
        for m in load_canary(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["canary_probe_pass_rate"] = m.probe_pass_rate
            inputs[k]["canary_avg_probe_score"] = m.avg_probe_score

    for f in conf_files:
        for m in load_conformance(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["conformance_pass_rate"] = m.pass_rate

    for f in integ_files:
        for m in load_integrity(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["integrity_probes"] = m.probes

    for f in img_canary_files:
        for m in load_image_canary(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["canary_probe_pass_rate"] = m.probe_pass_rate
            inputs[k]["canary_avg_probe_score"] = m.avg_probe_score
            inputs[k]["_image_channel"] = True
            inputs[k]["_image_canary_verdict"] = m.combined_verdict

    for f in img_conf_files:
        for m in load_image_conformance(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["_image_channel"] = True
            inputs[k]["image_api_compat_pass_rate"] = m.api_compat_pass_rate
            inputs[k]["image_output_valid_pass_rate"] = m.output_valid_pass_rate
            inputs[k]["image_safety_pass_rate"] = m.safety_pass_rate
            inputs[k]["image_zh_pass_rate"] = m.zh_pass_rate
            inputs[k]["image_en_pass_rate"] = m.en_pass_rate
            inputs[k]["image_phase_a_blocked"] = m.phase_a_blocked
            if m.p50_ms is not None:
                inputs[k]["image_p50_ms"] = m.p50_ms
            if m.p95_ms is not None:
                inputs[k]["image_p95_ms"] = m.p95_ms
            if m.rpm is not None:
                inputs[k]["image_rpm"] = m.rpm
            if m.success_rate is not None:
                inputs[k].setdefault("success_rate", m.success_rate)

    for f in img_lt_files:
        for m in load_image_loadtest(f):
            k = _merge(inputs, m.channel_name, m.channel_id, m.model)
            inputs[k]["_image_channel"] = True
            if m.p50_ms is not None:
                inputs[k]["image_p50_ms"] = m.p50_ms
            if m.p95_ms is not None:
                inputs[k]["image_p95_ms"] = m.p95_ms
            if m.rpm is not None:
                inputs[k]["image_rpm"] = m.rpm
            if m.success_rate is not None:
                inputs[k].setdefault("success_rate", m.success_rate)

    if not inputs:
        console.print("[red]No data found. Provide at least one result file.[/red]")
        return 2

    # Merge into single channel when --channel-id is set
    if args.channel_id is not None:
        merged: dict[str, dict] = {}
        for info in inputs.values():
            model = info["model"]
            mk = f"chid:{args.channel_id}||{model}"
            if mk not in merged:
                merged[mk] = {
                    "channel_name": args.channel_name or info.get("channel_name", ""),
                    "channel_id": args.channel_id,
                    "model": model,
                }
            for fld in ("success_rate", "ttft_p95_ms", "e2e_p95_ms", "throughput_toks",
                        "quality_pass_rate", "quality_avg_score",
                        "canary_probe_pass_rate", "canary_avg_probe_score",
                        "conformance_pass_rate", "integrity_probes"):
                if fld in info and fld not in merged[mk]:
                    merged[mk][fld] = info[fld]
        inputs = merged

    cards: list[ChannelScorecard] = []
    for info in inputs.values():
        is_image = info.get("_image_channel", False)

        if is_image:
            verdict = info.get("_image_canary_verdict", "")
            cap_map = {"PASS": 100.0, "MISMATCH": 0.0, "INCONCLUSIVE": 60.0}
            authenticity_cap = cap_map.get(verdict, 80.0)

            card = build_image_scorecard(
                channel_name=info["channel_name"],
                channel_id=info.get("channel_id"),
                model=info["model"],
                success_rate=info.get("success_rate"),
                p95_ms=info.get("image_p95_ms"),
                p50_ms=info.get("image_p50_ms"),
                rpm=info.get("image_rpm"),
                zh_pass_rate=info.get("image_zh_pass_rate"),
                en_pass_rate=info.get("image_en_pass_rate"),
                output_valid_rate=info.get("image_output_valid_pass_rate"),
                phase_a_blocked=info.get("image_phase_a_blocked", False),
                canary_pass_rate=info.get("canary_probe_pass_rate"),
                canary_avg_score=info.get("canary_avg_probe_score"),
                authenticity_cap=authenticity_cap,
                safety_pass_rate=info.get("image_safety_pass_rate"),
                api_compat_pass_rate=info.get("image_api_compat_pass_rate"),
            )
        else:
            honesty_rate = compliance_rate = None
            if "integrity_probes" in info:
                honesty_rate, compliance_rate = compute_integrity_rates(
                    info["integrity_probes"], info.get("model", "")
                )
            card = build_scorecard(
                channel_name=info["channel_name"],
                channel_id=info.get("channel_id"),
                model=info["model"],
                success_rate=info.get("success_rate"),
                ttft_p95_ms=info.get("ttft_p95_ms"),
                e2e_p95_ms=info.get("e2e_p95_ms"),
                throughput_toks=info.get("throughput_toks"),
                quality_pass_rate=info.get("quality_pass_rate"),
                quality_avg_score=info.get("quality_avg_score"),
                canary_probe_pass_rate=info.get("canary_probe_pass_rate"),
                canary_avg_probe_score=info.get("canary_avg_probe_score"),
                integrity_honesty_rate=honesty_rate,
                integrity_compliance_rate=compliance_rate,
                conformance_pass_rate=info.get("conformance_pass_rate"),
            )
        cards.append(card)

    write_json(cards, args.output)
    console.print(f"[green]wrote {args.output}[/green]")
    if args.markdown:
        write_markdown(cards, args.markdown)
        console.print(f"[green]wrote {args.markdown}[/green]")

    for card in sorted(cards, key=lambda c: c.composite_score, reverse=True):
        gc = {"A": "green", "B": "cyan", "C": "yellow", "D": "red", "F": "red bold"}.get(card.grade, "white")
        console.print(f"  [{gc}]{card.grade}[/{gc}] {card.composite_score:5.1f}  {card.channel_name} / {card.model}")
        if card.flags:
            for flag in card.flags:
                console.print(f"       [yellow]⚠ {flag}[/yellow]")

    return 0


def _read_loadtest_raw(path: Path) -> list[dict]:
    """Read loadtest JSON and return channels list for success_rate extraction."""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("channels", [])
    except Exception:
        return []


if __name__ == "__main__":
    sys.exit(main())
