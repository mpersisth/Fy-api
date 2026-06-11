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

IMAGE_WEIGHTS: dict[str, float] = {
    "availability": 0.20,
    "performance": 0.30,
    "quality": 0.20,
    "authenticity": 0.15,
    "compliance": 0.15,
}

GRADE_BANDS: list[tuple[float, str]] = [
    (90, "A"),
    (75, "B"),
    (60, "C"),
    (40, "D"),
    (0, "F"),
]

AVAILABILITY_GATE = 0.80
IMAGE_AVAILABILITY_GATE = 0.90

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

# Image-specific performance SLO anchors (image gen latency is 5-60s, not sub-second like text)
IMAGE_E2E_P95_BEST_MS = 5000.0
IMAGE_E2E_P95_WORST_MS = 60000.0
IMAGE_P50_BEST_MS = 5000.0
IMAGE_P50_WORST_MS = 20000.0
IMAGE_RPM_BEST = 10.0
IMAGE_RPM_WORST = 1.0
IMAGE_PERF_SUB_WEIGHTS = {"p95": 0.50, "rpm": 0.30, "p50": 0.20}


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


def score_image_performance(
    p95_ms: float | None = None,
    p50_ms: float | None = None,
    rpm: float | None = None,
    success_rate: float | None = None,
) -> DimensionResult:
    """Image-specific performance scoring per PRD §15."""
    parts: list[tuple[float, float]] = []
    details: list[str] = []
    if p95_ms is not None:
        s = _linear(p95_ms, IMAGE_E2E_P95_BEST_MS, IMAGE_E2E_P95_WORST_MS, lower_better=True)
        parts.append((s, IMAGE_PERF_SUB_WEIGHTS["p95"]))
        details.append(f"p95={p95_ms/1000:.1f}s")
    if rpm is not None:
        s = _linear(rpm, IMAGE_RPM_BEST, IMAGE_RPM_WORST, lower_better=False)
        parts.append((s, IMAGE_PERF_SUB_WEIGHTS["rpm"]))
        details.append(f"rpm={rpm:.1f}")
    if p50_ms is not None:
        s = _linear(p50_ms, IMAGE_P50_BEST_MS, IMAGE_P50_WORST_MS, lower_better=True)
        parts.append((s, IMAGE_PERF_SUB_WEIGHTS["p50"]))
        details.append(f"p50={p50_ms/1000:.1f}s")
    if not parts:
        return DimensionResult(
            score=0.0, weight=IMAGE_WEIGHTS["performance"], detail="no image perf data", available=False)
    total_w = sum(w for _, w in parts)
    score = _clamp(sum(s * (w / total_w) for s, w in parts))
    return DimensionResult(score=score, weight=IMAGE_WEIGHTS["performance"], detail=", ".join(details))


def score_image_quality(
    zh_pass_rate: float | None = None,
    en_pass_rate: float | None = None,
    output_valid_rate: float | None = None,
    phase_a_blocked: bool = False,
) -> DimensionResult:
    """Image quality scoring per PRD §14.2."""
    if phase_a_blocked:
        return DimensionResult(
            score=0.0, weight=IMAGE_WEIGHTS["quality"],
            detail="Phase A blocked — quality score = 0",
        )
    parts: list[tuple[float, float]] = []
    details: list[str] = []
    if zh_pass_rate is not None:
        parts.append((_clamp(zh_pass_rate * 100.0), 0.40))
        details.append(f"zh={zh_pass_rate:.0%}")
    if en_pass_rate is not None:
        parts.append((_clamp(en_pass_rate * 100.0), 0.30))
        details.append(f"en={en_pass_rate:.0%}")
    if output_valid_rate is not None:
        parts.append((_clamp(output_valid_rate * 100.0), 0.30))
        details.append(f"output_valid={output_valid_rate:.0%}")
    if not parts:
        return DimensionResult(
            score=0.0, weight=IMAGE_WEIGHTS["quality"], detail="no data", available=False)
    total_w = sum(w for _, w in parts)
    score = _clamp(sum(s * (w / total_w) for s, w in parts))
    return DimensionResult(score=score, weight=IMAGE_WEIGHTS["quality"], detail=", ".join(details))


def score_image_authenticity(
    canary_pass_rate: float | None = None,
    canary_avg_score: float | None = None,
    confidence_cap: float = 100.0,
) -> DimensionResult:
    """Image authenticity scoring per PRD §14.2.

    confidence_cap: max score based on verification method:
      100 = Vendor verified or cross-channel verified
      80  = Fingerprint verified
      60  = Inconclusive
      0   = Mismatch
    """
    if confidence_cap <= 0:
        return DimensionResult(
            score=0.0, weight=IMAGE_WEIGHTS["authenticity"],
            detail="MISMATCH — authenticity = 0",
        )
    if canary_pass_rate is None and canary_avg_score is None:
        return DimensionResult(
            score=0.0, weight=IMAGE_WEIGHTS["authenticity"], detail="no canary data", available=False)
    raw_score = 0.0
    details = []
    if canary_pass_rate is not None:
        raw_score += canary_pass_rate * 50.0
        details.append(f"pass_rate={canary_pass_rate:.0%}")
    if canary_avg_score is not None:
        raw_score += canary_avg_score * 50.0
        details.append(f"avg_score={canary_avg_score:.2f}")
    score = _clamp(min(raw_score, confidence_cap))
    details.append(f"cap={confidence_cap:.0f}")
    return DimensionResult(score=score, weight=IMAGE_WEIGHTS["authenticity"], detail=", ".join(details))


def score_image_compliance(
    safety_pass_rate: float | None = None,
    api_compat_pass_rate: float | None = None,
) -> DimensionResult:
    """Image compliance scoring per PRD §14.2."""
    parts: list[tuple[float, float]] = []
    details: list[str] = []
    if safety_pass_rate is not None:
        parts.append((_clamp(safety_pass_rate * 100.0), 0.60))
        details.append(f"safety={safety_pass_rate:.0%}")
    if api_compat_pass_rate is not None:
        parts.append((_clamp(api_compat_pass_rate * 100.0), 0.40))
        details.append(f"api_compat={api_compat_pass_rate:.0%}")
    if not parts:
        return DimensionResult(
            score=0.0, weight=IMAGE_WEIGHTS["compliance"], detail="no data", available=False)
    total_w = sum(w for _, w in parts)
    score = _clamp(sum(s * (w / total_w) for s, w in parts))
    return DimensionResult(score=score, weight=IMAGE_WEIGHTS["compliance"], detail=", ".join(details))


def build_image_scorecard(
    channel_name: str,
    channel_id: int | None,
    model: str,
    *,
    success_rate: float | None = None,
    p95_ms: float | None = None,
    p50_ms: float | None = None,
    rpm: float | None = None,
    zh_pass_rate: float | None = None,
    en_pass_rate: float | None = None,
    output_valid_rate: float | None = None,
    phase_a_blocked: bool = False,
    canary_pass_rate: float | None = None,
    canary_avg_score: float | None = None,
    authenticity_cap: float = 100.0,
    safety_pass_rate: float | None = None,
    api_compat_pass_rate: float | None = None,
) -> ChannelScorecard:
    card = ChannelScorecard(channel_name=channel_name, channel_id=channel_id, model=model)

    # Availability (gate at 90% for images; exactly 90% passes)
    if success_rate is not None:
        if success_rate < IMAGE_AVAILABILITY_GATE:
            card.dimensions["availability"] = DimensionResult(
                score=0.0, weight=IMAGE_WEIGHTS["availability"],
                detail=f"success_rate={success_rate:.1%} (below {IMAGE_AVAILABILITY_GATE:.0%} gate)",
            )
            card.gated_out = True
            card.flags.append(f"availability below {IMAGE_AVAILABILITY_GATE:.0%} gate")
        else:
            gate_range = 1.0 - IMAGE_AVAILABILITY_GATE
            score = _clamp((success_rate - IMAGE_AVAILABILITY_GATE) / gate_range * 100.0)
            card.dimensions["availability"] = DimensionResult(
                score=score, weight=IMAGE_WEIGHTS["availability"],
                detail=f"success_rate={success_rate:.1%}",
            )
    else:
        card.dimensions["availability"] = DimensionResult(
            score=0.0, weight=IMAGE_WEIGHTS["availability"], detail="no data", available=False)

    # Performance
    card.dimensions["performance"] = score_image_performance(p95_ms, p50_ms, rpm, success_rate)

    # Quality
    card.dimensions["quality"] = score_image_quality(
        zh_pass_rate, en_pass_rate, output_valid_rate, phase_a_blocked)

    # Authenticity
    card.dimensions["authenticity"] = score_image_authenticity(
        canary_pass_rate, canary_avg_score, authenticity_cap)
    if canary_pass_rate == 0.0:
        card.flags.append("all image canary probes failed — suspected model swap")

    # Compliance
    card.dimensions["compliance"] = score_image_compliance(safety_pass_rate, api_compat_pass_rate)

    card.compute_composite()
    return card


def score_availability(connectivity_rate: float) -> DimensionResult:
    """Score availability based on C=1 (no-concurrency) success rate only."""
    if connectivity_rate < AVAILABILITY_GATE:
        return DimensionResult(
            score=0.0,
            weight=WEIGHTS["availability"],
            detail=f"connectivity={connectivity_rate:.1%} (below {AVAILABILITY_GATE:.0%} gate)",
        )
    score = _clamp((connectivity_rate - AVAILABILITY_GATE) / (1.0 - AVAILABILITY_GATE) * 100.0)
    return DimensionResult(
        score=score, weight=WEIGHTS["availability"],
        detail=f"connectivity={connectivity_rate:.1%}",
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
    connectivity_rate: float | None = None,
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

    # Availability — based on C=1 success rate only
    if connectivity_rate is not None:
        card.dimensions["availability"] = score_availability(connectivity_rate)
        if connectivity_rate < AVAILABILITY_GATE:
            card.gated_out = True
            card.flags.append(f"connectivity below {AVAILABILITY_GATE:.0%} gate")
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
