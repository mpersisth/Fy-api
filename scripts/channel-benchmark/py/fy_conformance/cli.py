"""fy-conformance CLI."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fy_conformance import __version__
from fy_conformance.config import load as load_config
from fy_conformance.dataset import load as load_dataset
from fy_conformance.runner import aggregate, run_all, to_jsonl, to_markdown


def _argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fy-conformance",
        description="Protocol-conformance test harness for Fy-api channels.",
    )
    p.add_argument("-c", "--config", required=True, help="path to YAML config")
    p.add_argument(
        "--category",
        default=None,
        help="only run cases whose `category` matches (e.g. param_validation_auto)",
    )
    p.add_argument(
        "--id",
        default=None,
        help="only run cases whose `id` matches exactly (useful for debugging one)",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="stop after this many cases (useful for smoke runs)",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _argparser().parse_args(argv)
    cfg = load_config(args.config)
    cases = load_dataset(cfg.dataset)
    if args.category:
        cases = [c for c in cases if c.category == args.category]
    if args.id:
        cases = [c for c in cases if c.id == args.id]
    if args.limit:
        cases = cases[: args.limit]

    if not cases:
        print("No cases match filters.", file=sys.stderr)
        return 2

    print(
        f"fy-conformance: {len(cases)} cases against "
        f"{cfg.gateway.base_url} (model={cfg.target.model}, backend={cfg.target.backend or '(any)'})",
        file=sys.stderr,
    )

    results = asyncio.run(run_all(cfg, cases))
    summary = aggregate(results)

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = cfg.output_dir / f"conformance-{cfg.target.model}-{stamp}"
    base.with_suffix(".jsonl").write_text(to_jsonl(results) + "\n", encoding="utf-8")
    base.with_suffix(".summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    base.with_suffix(".md").write_text(
        to_markdown(results, summary),
        encoding="utf-8",
    )

    # Also dump a one-line summary to stdout for shell pipelines.
    print(
        f"total={summary['total']} executed={summary['executed']} pass={summary['pass']} "
        f"fail={summary['fail']} error={summary['error']} skip={summary['skip']} "
        f"pass_rate={summary['pass_rate']*100:.1f}%",
    )
    print(f"wrote {base}.{{jsonl,summary.json,md}}", file=sys.stderr)

    # Non-zero exit if anything failed — useful for CI gating.
    return 0 if summary["fail"] == 0 and summary["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
