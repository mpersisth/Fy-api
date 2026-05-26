"""Core scoring logic — SLO-anchored, absolute rating (v0.2).

Five dimensions: availability, performance, quality, authenticity, compliance.
Scoring standard is identical for single-model and multi-channel scenarios.
"""

from __future__ import annotations

from dataclasses import dataclass, field


WEIGHTS: dict[str, float] = {
    "availability": 0.15,
    "performance": 0.25,
    "quality": 0.25,
    "authenticity": 0.20,
    "compliance": 0.15,
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

# Integrity probes mapped to dimensions
_HONESTY_PROBES = {"token_inflation", "determinism", "cache_integrity"}
_COMPLIANCE_PROBES = {"stream_repackaging", "tool_use_passthrough", "content_filtering"}
# PLACEHOLDER_SCORER_CONTINUE


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
# PLACEHOLDER_SCORER_FUNCTIONS


def score_availability(success_rate: float) -> DimensionResult:
    if success_rate < AVAILABILITY_GATE:
        return DimensionResult(
            score=0.0,
            weight=WEIGHTS["availability"],
            detail=f"success_rate={success_rate:.1%} (below {AVAILABILITY_GATE:.0%} gate)",
        )
    score = _clamp((success_rate - AVAILABILITY_GATE) / (1.0 - AVAILABILITY_GATE) * 100.0)
    return DimensionResult(
        score=score, weight=WEIGHTS["availability"],
        detail=f"success_rate={success_rate:.1%}",
    )


def score_performance(
    ttft_p95_ms: float | None,
    e2e_p95_ms: float | None,
    throughput_toks: float | None,
) -> DimensionResult:
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
    return DimensionResult(score=score, weight=WEIGHTS["performance"], detail=", ".join(details))


def score_quality(pass_rate: float, avg_score: float) -> DimensionResult:
    score = _clamp(pass_rate * 0.6 * 100.0 + avg_score * 0.4 * 100.0)
    return DimensionResult(
        score=score, weight=WEIGHTS["quality"],
        detail=f"pass_rate={pass_rate:.0%}, avg_score={avg_score:.2f}",
    )


def score_authenticity(
    canary_pass_rate: float | None,
    canary_avg_score: float | None,
    integrity_honesty_rate: float | None,
) -> DimensionResult:
    parts: list[tuple[float, float]] = []
    details: list[str] = []
    if canary_pass_rate is not None and canary_avg_score is not None:
        s = canary_pass_rate * 0.5 * 100.0 + canary_avg_score * 0.5 * 100.0
        parts.append((_clamp(s), 0.50))
        details.append(f"canary={canary_pass_rate:.0%}")
    if integrity_honesty_rate is not None:
        parts.append((_clamp(integrity_honesty_rate * 100.0), 0.50))
        details.append(f"integrity_honesty={integrity_honesty_rate:.0%}")
    if not parts:
        return DimensionResult(score=0.0, weight=WEIGHTS["authenticity"], detail="no data", available=False)
    total_w = sum(w for _, w in parts)
    score = _clamp(sum(s * (w / total_w) for s, w in parts))
    return DimensionResult(score=score, weight=WEIGHTS["authenticity"], detail=", ".join(details))
# PLACEHOLDER_SCORER_BUILD


def score_compliance(
    conformance_pass_rate: float | None,
    integrity_compliance_rate: float | None,
) -> DimensionResult:
    parts: list[tuple[float, float]] = []
    details: list[str] = []
    if conformance_pass_rate is not None:
        parts.append((_clamp(conformance_pass_rate * 100.0), 0.60))
        details.append(f"conformance={conformance_pass_rate:.0%}")
    if integrity_compliance_rate is not None:
        parts.append((_clamp(integrity_compliance_rate * 100.0), 0.40))
        details.append(f"integrity_compliance={integrity_compliance_rate:.0%}")
    if not parts:
        return DimensionResult(score=0.0, weight=WEIGHTS["compliance"], detail="no data", available=False)
    total_w = sum(w for _, w in parts)
    score = _clamp(sum(s * (w / total_w) for s, w in parts))
    return DimensionResult(score=score, weight=WEIGHTS["compliance"], detail=", ".join(details))


def compute_integrity_rates(
    probes: list[dict], model: str = "",
) -> tuple[float | None, float | None]:
    """Split integrity probes into honesty and compliance rates.

    Returns (honesty_rate, compliance_rate) as 0.0-1.0 or None if no data.
    For non-Anthropic models, tool_use_passthrough FAIL is excluded from compliance.
    """
    honesty_total = honesty_pass = 0
    compliance_total = compliance_pass = 0
    is_anthropic = any(x in model.lower() for x in ("claude", "anthropic"))

    for p in probes:
        name = p.get("probe_name", "")
        passed = p.get("passed", False)
        skipped = p.get("details", {}).get("skipped", False)
        if skipped:
            continue
        if name in _HONESTY_PROBES:
            honesty_total += 1
            if passed:
                honesty_pass += 1
        elif name in _COMPLIANCE_PROBES:
            if name == "tool_use_passthrough" and not is_anthropic and not passed:
                continue
            compliance_total += 1
            if passed:
                compliance_pass += 1

    honesty_rate = honesty_pass / honesty_total if honesty_total else None
    compliance_rate = compliance_pass / compliance_total if compliance_total else None
    return honesty_rate, compliance_rate


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
    integrity_honesty_rate: float | None = None,
    integrity_compliance_rate: float | None = None,
    conformance_pass_rate: float | None = None,
) -> ChannelScorecard:
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
    card.dimensions["authenticity"] = score_authenticity(
        canary_probe_pass_rate, canary_avg_probe_score, integrity_honesty_rate
    )
    if canary_probe_pass_rate == 0.0:
        card.flags.append("all canary probes failed — suspected model swap")

    # Compliance
    card.dimensions["compliance"] = score_compliance(
        conformance_pass_rate, integrity_compliance_rate
    )

    card.compute_composite()
    return card
