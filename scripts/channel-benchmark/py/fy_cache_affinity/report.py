"""Report writers: JSON, Markdown comparison table, and matplotlib curve."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .runner import BenchmarkResult

_COLORS = ["#2ecc71", "#3498db", "#e74c3c", "#f39c12"]


def write_reports(result: BenchmarkResult, formats: list[str], output_dir: str) -> list[Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    written: list[Path] = []

    for fmt in formats:
        if fmt == "json":
            written.append(_write_json(result, out, ts))
        elif fmt == "markdown":
            written.append(_write_md(result, out, ts))
        elif fmt == "png":
            written.append(_write_png(result, out, ts))

    return written


def _write_json(result: BenchmarkResult, out: Path, ts: str) -> Path:
    path = out / f"raw_{ts}.json"
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": result.model,
        "base_url": result.base_url,
        "groups": [],
    }
    for g in result.groups:
        group_doc = {
            "name": g.name,
            "runs": [
                {
                    "seed": r.seed,
                    "session_id": r.session_id,
                    "turns": [
                        {
                            "turn": t.turn,
                            "prompt_tokens": t.prompt_tokens,
                            "cached_tokens": t.cached_tokens,
                            "cache_ratio": round(t.cache_ratio, 4),
                            "ttft_ms": round(t.ttft_ms, 1),
                            "e2e_ms": round(t.e2e_ms, 1),
                        }
                        for t in r.turns
                    ],
                }
                for r in g.runs
            ],
            "aggregates": [
                {
                    "turn": a.turn,
                    "avg_cache_ratio": round(a.avg_cache_ratio, 4),
                    "min_cache_ratio": round(a.min_cache_ratio, 4),
                    "max_cache_ratio": round(a.max_cache_ratio, 4),
                    "avg_prompt_tokens": round(a.avg_prompt_tokens, 1),
                    "avg_ttft_ms": round(a.avg_ttft_ms, 1),
                }
                for a in g.aggregates
            ],
        }
        doc["groups"].append(group_doc)

    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    return path


def _write_md(result: BenchmarkResult, out: Path, ts: str) -> Path:
    path = out / f"comparison_{ts}.md"
    lines = [f"# Cache Affinity Benchmark — {result.model}\n"]
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")
    lines.append(f"Gateway: {result.base_url}\n\n")

    max_turns = max((len(g.aggregates) for g in result.groups), default=0)
    header = "| Turn | Tokens |" + "|".join(f" {g.name} " for g in result.groups) + "|"
    sep = "|------|--------|" + "|".join("--------" for _ in result.groups) + "|"
    lines.append(header)
    lines.append(sep)

    for t_idx in range(max_turns):
        tokens_str = ""
        cells: list[str] = []
        for g in result.groups:
            if t_idx < len(g.aggregates):
                agg = g.aggregates[t_idx]
                cells.append(f" {agg.avg_cache_ratio:.1%} ")
                if not tokens_str:
                    tokens_str = f"~{int(agg.avg_prompt_tokens)}"
            else:
                cells.append(" - ")
        lines.append(f"| {t_idx+1} | {tokens_str} |" + "|".join(cells) + "|")

    lines.append("\n\n## Conclusion\n")
    for g in result.groups:
        if g.aggregates:
            final = g.aggregates[-1]
            lines.append(f"- **{g.name}**: 最终 cache ratio = {final.avg_cache_ratio:.1%} (第{final.turn}轮, ~{int(final.avg_prompt_tokens)} tokens)")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_png(result: BenchmarkResult, out: Path, ts: str) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = out / f"curve_{ts}.png"
    fig, ax = plt.subplots(figsize=(12, 6))

    for i, g in enumerate(result.groups):
        if not g.aggregates:
            continue
        turns = [a.turn for a in g.aggregates]
        ratios = [a.avg_cache_ratio * 100 for a in g.aggregates]
        mins = [a.min_cache_ratio * 100 for a in g.aggregates]
        maxs = [a.max_cache_ratio * 100 for a in g.aggregates]
        color = _COLORS[i % len(_COLORS)]

        ax.plot(turns, ratios, label=g.name, color=color, linewidth=2)
        ax.fill_between(turns, mins, maxs, alpha=0.15, color=color)

    ax.set_xlabel("Turn (轮次)")
    ax.set_ylabel("Cache Hit Ratio (%)")
    ax.set_title(f"Cache Affinity Benchmark — {result.model}")
    ax.legend(loc="lower right")
    ax.set_ylim(0, 105)
    ax.grid(True, alpha=0.3)

    if result.groups and result.groups[0].aggregates:
        agg = result.groups[0].aggregates
        ax2 = ax.twiny()
        token_ticks = [int(a.avg_prompt_tokens) for a in agg]
        ax2.set_xlim(ax.get_xlim())
        step = max(1, len(agg) // 6)
        ax2.set_xticks([agg[i].turn for i in range(0, len(agg), step)])
        ax2.set_xticklabels([f"~{token_ticks[i]}" for i in range(0, len(agg), step)])
        ax2.set_xlabel("Cumulative Prompt Tokens")

    plt.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
