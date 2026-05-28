"""Per-turn and aggregate cache ratio computation."""

from __future__ import annotations

from dataclasses import dataclass

from .conversation import ConversationResult


@dataclass
class TurnAggregate:
    turn: int
    samples: int
    avg_cache_ratio: float
    min_cache_ratio: float
    max_cache_ratio: float
    avg_prompt_tokens: float
    avg_cached_tokens: float
    avg_ttft_ms: float
    avg_e2e_ms: float


def aggregate_runs(runs: list[ConversationResult]) -> list[TurnAggregate]:
    if not runs:
        return []

    max_turns = max(len(r.turns) for r in runs)
    aggregates: list[TurnAggregate] = []

    for t_idx in range(max_turns):
        ratios: list[float] = []
        prompt_tokens: list[int] = []
        cached_tokens: list[int] = []
        ttfts: list[float] = []
        e2es: list[float] = []

        for run in runs:
            if t_idx < len(run.turns):
                turn = run.turns[t_idx]
                ratios.append(turn.cache_ratio)
                prompt_tokens.append(turn.prompt_tokens)
                cached_tokens.append(turn.cached_tokens)
                ttfts.append(turn.ttft_ms)
                e2es.append(turn.e2e_ms)

        if not ratios:
            continue

        aggregates.append(TurnAggregate(
            turn=t_idx + 1,
            samples=len(ratios),
            avg_cache_ratio=sum(ratios) / len(ratios),
            min_cache_ratio=min(ratios),
            max_cache_ratio=max(ratios),
            avg_prompt_tokens=sum(prompt_tokens) / len(prompt_tokens),
            avg_cached_tokens=sum(cached_tokens) / len(cached_tokens),
            avg_ttft_ms=sum(ttfts) / len(ttfts),
            avg_e2e_ms=sum(e2es) / len(e2es),
        ))

    return aggregates
