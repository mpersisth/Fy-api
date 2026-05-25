"""Core scoring logic — SLO-anchored, absolute rating."""

from __future__ import annotations

from dataclasses import dataclass, field


WEIGHTS: dict[str, float] = {
    "availability": 0.20,
    "performance": 0.30,
    "quality": 0.35,
    "authenticity": 0.15,
}

GRADE_BANDS: list[tuple[float, str]] = [
    (90, "A"),
    (75, "B"),
    (60, "C"),
    (40, "D"),
    (0, "F"),
]

AVAILABILITY_GATE = 0.95

# Performance SLO anchors
TTFT_P95_BEST_MS = 500.0
TTFT_P95_WORST_MS = 3000.0
E2E_P95_BEST_MS = 5000.0
E2E_P95_WORST_MS = 30000.0
THROUGHPUT_BEST_TOKS = 80.0
THROUGHPUT_WORST_TOKS = 10.0

PERF_SUB_WEIGHTS = {"ttft_p95": 0.40, "e2e_p95": 0.30, "throughput": 0.30}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _linear(value: float, best: float, worst: float, lower_better: bool) -> float:
    if lower_better:
        if value <= best:
            return 100.0
        if value >= worst:
            return 0.0
        return (worst - value) / (worst - best) * 100.0
    else:
        if value >= best:
            return 100.0
        if value <= worst:
            return 0.0
        return (value - worst) / (best - worst) * 100.0


def grade_for(score: float) -> str:
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


@dataclass
class DimensionResult:
    score: float
    weight: float
    detail: str
    available: bool = True


@dataclass
class ChannelScorecard:
    channel_name: str
    channel_id: int | None
    model: str
    dimensions: dict[str, DimensionResult] = field(default_factory=dict)
    composite_score: float = 0.0
    grade: str = "F"
    flags: list[str] = field(default_factory=list)
    gated_out: bool = False

    def compute_composite(self) -> None:
        if self.gated_out:
            self.composite_score = 0.0
            self.grade = "F"
            return
        active = {k: v for k, v in self.dimensions.items() if v.available}
        if not active:
            self.composite_score = 0.0
            self.grade = "F"
            return
        total_weight = sum(v.weight for v in active.values())
        if total_weight == 0:
            self.composite_score = 0.0
            self.grade = "F"
            return
        self.composite_score = sum(
            v.score * (v.weight / total_weight) for v in active.values()
        )
        self.grade = grade_for(self.composite_score)


def score_availability(success_rate: float) -> DimensionResult:
    """Score availability from success rate (0.0-1.0)."""
    if success_rate < AVAILABILITY_GATE:
        return DimensionResult(
            score=0.0,
            weight=WEIGHTS["availability"],
            detail=f"success_rate={success_rate:.1%} (below {AVAILABILITY_GATE:.0%} gate)",
        )
    score = _clamp((success_rate - AVAILABILITY_GATE) / (1.0 - AVAILABILITY_GATE) * 100.0)
    return DimensionResult(
        score=score,
        weight=WEIGHTS["availability"],
        detail=f"success_rate={success_rate:.1%}",
    )


def score_performance(
    ttft_p95_ms: float | None,
    e2e_p95_ms: float | None,
    throughput_toks: float | None,
) -> DimensionResult:
    """Score performance from loadtest metrics."""
    parts: list[tuple[float, float]] = []
    details: list[str] = []

    if ttft_p95_ms is not None:
        s = _linear(ttft_p95_ms, TTFT_P95_BEST_MS, TTFT_P95_WORST_MS, lower_better=True)
        parts.append((s, PERF_SUB_WEIGHTS["ttft_p95"]))
        details.append(f"ttft_p95={ttft_p95_ms:.0f}ms")
    if e2e_p95_ms is not None:
        s = _linear(e2e_p95_ms, E2E_P95_BEST_MS, E2E_P95_WORST_MS, lower_better=True)
        parts.append((s, PERF_SUB_WEIGHTS["e2e_p95"]))
        details.append(f"e2e_p95={e2e_p95_ms:.0f}ms")
    if throughput_toks is not None:
        s = _linear(throughput_toks, THROUGHPUT_BEST_TOKS, THROUGHPUT_WORST_TOKS, lower_better=False)
        parts.append((s, PERF_SUB_WEIGHTS["throughput"]))
        details.append(f"tok_s={throughput_toks:.1f}")

    if not parts:
        return DimensionResult(score=0.0, weight=WEIGHTS["performance"], detail="no data", available=False)

    total_w = sum(w for _, w in parts)
    score = _clamp(sum(s * (w / total_w) for s, w in parts))
    return DimensionResult(
        score=score,
        weight=WEIGHTS["performance"],
        detail=", ".join(details),
    )


def score_quality(pass_rate: float, avg_score: float) -> DimensionResult:
    """Score quality from fy-quality results. Both inputs are 0.0-1.0."""
    score = _clamp(pass_rate * 0.6 * 100.0 + avg_score * 0.4 * 100.0)
    return DimensionResult(
        score=score,
        weight=WEIGHTS["quality"],
        detail=f"pass_rate={pass_rate:.0%}, avg_score={avg_score:.2f}",
    )


def score_authenticity(
    probe_pass_rate: float, avg_probe_score: float
) -> DimensionResult:
    """Score authenticity from fy-canary results. Both inputs are 0.0-1.0."""
    score = _clamp(probe_pass_rate * 0.5 * 100.0 + avg_probe_score * 0.5 * 100.0)
    return DimensionResult(
        score=score,
        weight=WEIGHTS["authenticity"],
        detail=f"probe_pass={probe_pass_rate:.0%}, avg_probe_score={avg_probe_score:.2f}",
    )


def build_scorecard(
    channel_name: str,
    channel_id: int | None,
    model: str,
    *,
    success_rate: float | None = None,
    ttft_p95_ms: float | None = None,
    e2e_p95_ms: float | None = None,
    throughput_toks: float | None = None,
    quality_pass_rate: float | None = None,
    quality_avg_score: float | None = None,
    canary_probe_pass_rate: float | None = None,
    canary_avg_probe_score: float | None = None,
) -> ChannelScorecard:
    """Build a complete scorecard for one (channel, model) pair."""
    card = ChannelScorecard(channel_name=channel_name, channel_id=channel_id, model=model)

    # Availability
    if success_rate is not None:
        card.dimensions["availability"] = score_availability(success_rate)
        if success_rate < AVAILABILITY_GATE:
            card.gated_out = True
            card.flags.append(f"availability below {AVAILABILITY_GATE:.0%} gate")
    else:
        card.dimensions["availability"] = DimensionResult(
            score=0.0, weight=WEIGHTS["availability"], detail="no data", available=False
        )

    # Performance
    card.dimensions["performance"] = score_performance(ttft_p95_ms, e2e_p95_ms, throughput_toks)

    # Quality
    if quality_pass_rate is not None and quality_avg_score is not None:
        card.dimensions["quality"] = score_quality(quality_pass_rate, quality_avg_score)
    else:
        card.dimensions["quality"] = DimensionResult(
            score=0.0, weight=WEIGHTS["quality"], detail="no data", available=False
        )

    # Authenticity
    if canary_probe_pass_rate is not None and canary_avg_probe_score is not None:
        card.dimensions["authenticity"] = score_authenticity(
            canary_probe_pass_rate, canary_avg_probe_score
        )
        if canary_probe_pass_rate == 0.0:
            card.flags.append("all canary probes failed — suspected model swap")
    else:
        card.dimensions["authenticity"] = DimensionResult(
            score=0.0, weight=WEIGHTS["authenticity"], detail="no data", available=False
        )

    card.compute_composite()
    return card
