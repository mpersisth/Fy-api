"""Aggregation and percentile computation for ChatResult batches."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .client import ChatResult
from .config import Slo


@dataclass
class LatencyStats:
    """Time stats in MILLISECONDS (for human readability in reports)."""

    samples: int = 0
    min_ms: float = 0.0
    max_ms: float = 0.0
    avg_ms: float = 0.0
    p50_ms: float = 0.0
    p90_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    stddev_ms: float = 0.0


@dataclass
class ThroughputStats:
    samples: int = 0
    min: float = 0.0
    max: float = 0.0
    avg: float = 0.0
    p50: float = 0.0


@dataclass
class LevelAggregate:
    """One concurrency level's worth of results."""

    concurrency: int
    total: int
    ok: int
    failed: int
    success_rate_pct: float
    wall_time_s: float
    throughput_req_per_s: float
    aggregate_tok_per_s: float  # Σ completion_tokens / wall_time — system decode throughput

    e2e: LatencyStats = field(default_factory=LatencyStats)
    ttft: LatencyStats = field(default_factory=LatencyStats)
    itl: LatencyStats = field(default_factory=LatencyStats)
    tpot: LatencyStats = field(default_factory=LatencyStats)

    per_request_tok_per_s: ThroughputStats = field(default_factory=ThroughputStats)
    avg_prompt_tokens: float = 0.0
    avg_completion_tokens: float = 0.0
    avg_cached_tokens: float = 0.0

    # Rate metrics (per-minute)
    rpm: float = 0.0
    input_tpm: float = 0.0
    output_tpm: float = 0.0
    total_tpm: float = 0.0

    # Error categorization
    errors_429: int = 0
    errors_5xx: int = 0
    errors_timeout: int = 0
    error_rate_429_pct: float = 0.0
    error_rate_5xx_pct: float = 0.0
    error_rate_timeout_pct: float = 0.0

    goodput_req_per_s: float | None = None  # None = no SLO configured
    error_breakdown: dict[str, int] = field(default_factory=dict)


def percentile(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolation percentile (matches NumPy default and our Go tool)."""
    if not sorted_vals:
        return 0.0
    if p <= 0:
        return sorted_vals[0]
    if p >= 100:
        return sorted_vals[-1]
    idx = (p / 100.0) * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if lo == hi:
        return sorted_vals[lo]
    w = idx - lo
    return sorted_vals[lo] * (1 - w) + sorted_vals[hi] * w


def _latency_stats(values_s: list[float]) -> LatencyStats:
    if not values_s:
        return LatencyStats()
    ms = sorted(v * 1000.0 for v in values_s)
    avg = sum(ms) / len(ms)
    var = sum((v - avg) ** 2 for v in ms) / max(len(ms) - 1, 1) if len(ms) > 1 else 0.0
    return LatencyStats(
        samples=len(ms),
        min_ms=ms[0],
        max_ms=ms[-1],
        avg_ms=avg,
        p50_ms=percentile(ms, 50),
        p90_ms=percentile(ms, 90),
        p95_ms=percentile(ms, 95),
        p99_ms=percentile(ms, 99),
        stddev_ms=math.sqrt(var),
    )


def _throughput_stats(values: list[float]) -> ThroughputStats:
    if not values:
        return ThroughputStats()
    sorted_v = sorted(values)
    return ThroughputStats(
        samples=len(sorted_v),
        min=sorted_v[0],
        max=sorted_v[-1],
        avg=sum(sorted_v) / len(sorted_v),
        p50=percentile(sorted_v, 50),
    )


def aggregate_level(
    concurrency: int,
    results: list[ChatResult],
    wall_time_s: float,
    slo: Slo | None = None,
) -> LevelAggregate:
    """Summarize all requests at one concurrency level.

    wall_time_s is the real-clock duration the level was running — used to
    compute system-wide req/s and token/s. Passed in because only the runner
    knows the true start/end markers.
    """
    total = len(results)
    ok = [r for r in results if r.success]
    failed = total - len(ok)

    e2e_vals = [r.e2e_s for r in ok]
    ttft_vals = [r.ttft_s for r in ok if r.ttft_s > 0]
    itl_vals = [g for r in ok for g in r.inter_token_gaps_s]
    tpot_vals = [r.tpot_s() for r in ok if r.tpot_s() > 0]

    completion_tokens_total = sum(r.usage.completion_tokens for r in ok)
    prompt_tokens_total = sum(r.usage.prompt_tokens for r in ok)
    cached_tokens_total = sum(r.usage.cached_tokens for r in ok)

    per_req_tps = [r.tokens_per_sec() for r in ok if r.tokens_per_sec() > 0]

    errors: dict[str, int] = {}
    n_429 = 0
    n_5xx = 0
    n_timeout = 0
    for r in results:
        if not r.success:
            sig = r.error or "unknown error"
            errors[sig] = errors.get(sig, 0) + 1
            if r.http_status == 429:
                n_429 += 1
            elif r.http_status >= 500:
                n_5xx += 1
            if sig.startswith("timeout:"):
                n_timeout += 1

    minutes = wall_time_s / 60.0 if wall_time_s > 0 else 0.0
    rpm = len(ok) / minutes if minutes > 0 else 0.0
    input_tpm = prompt_tokens_total / minutes if minutes > 0 else 0.0
    output_tpm = completion_tokens_total / minutes if minutes > 0 else 0.0
    total_tpm = input_tpm + output_tpm

    agg = LevelAggregate(
        concurrency=concurrency,
        total=total,
        ok=len(ok),
        failed=failed,
        success_rate_pct=100.0 * len(ok) / total if total else 0.0,
        wall_time_s=wall_time_s,
        throughput_req_per_s=len(ok) / wall_time_s if wall_time_s > 0 else 0.0,
        aggregate_tok_per_s=completion_tokens_total / wall_time_s if wall_time_s > 0 else 0.0,
        e2e=_latency_stats(e2e_vals),
        ttft=_latency_stats(ttft_vals),
        itl=_latency_stats(itl_vals),
        tpot=_latency_stats(tpot_vals),
        per_request_tok_per_s=_throughput_stats(per_req_tps),
        avg_prompt_tokens=prompt_tokens_total / len(ok) if ok else 0.0,
        avg_completion_tokens=completion_tokens_total / len(ok) if ok else 0.0,
        avg_cached_tokens=cached_tokens_total / len(ok) if ok else 0.0,
        rpm=rpm,
        input_tpm=input_tpm,
        output_tpm=output_tpm,
        total_tpm=total_tpm,
        errors_429=n_429,
        errors_5xx=n_5xx,
        errors_timeout=n_timeout,
        error_rate_429_pct=100.0 * n_429 / total if total else 0.0,
        error_rate_5xx_pct=100.0 * n_5xx / total if total else 0.0,
        error_rate_timeout_pct=100.0 * n_timeout / total if total else 0.0,
        error_breakdown=errors,
    )

    if slo and any(v is not None for v in (slo.ttft_p95_ms, slo.itl_p95_ms, slo.e2e_p95_ms)):
        good_count = sum(1 for r in ok if _meets_slo(r, slo))
        agg.goodput_req_per_s = good_count / wall_time_s if wall_time_s > 0 else 0.0

    return agg


def _meets_slo(r: ChatResult, slo: Slo) -> bool:
    """Per-request SLO evaluation for goodput.

    We approximate p95 SLOs at the per-request level by comparing that
    request's value to the threshold — over many requests this converges on
    "fraction of requests meeting the p95 target", which is what goodput
    typically aims at.
    """
    if slo.ttft_p95_ms is not None and r.streamed:
        if r.ttft_s * 1000.0 > slo.ttft_p95_ms:
            return False
    if slo.itl_p95_ms is not None and r.inter_token_gaps_s:
        worst_itl_ms = max(r.inter_token_gaps_s) * 1000.0
        if worst_itl_ms > slo.itl_p95_ms:
            return False
    if slo.e2e_p95_ms is not None:
        if r.e2e_s * 1000.0 > slo.e2e_p95_ms:
            return False
    return True


# ---------------------------------------------------------------------------
# Ceiling finder result
# ---------------------------------------------------------------------------


@dataclass
class CeilingResult:
    measured_rpm: float = 0.0
    measured_input_tpm: float = 0.0
    measured_output_tpm: float = 0.0
    measured_total_tpm: float = 0.0
    header_rpm_limit: float | None = None
    header_tpm_limit: float | None = None
    limit_type: str = "unknown"   # "rpm" | "tpm" | "both" | "unknown"
    confidence: str = "low"       # "low" | "medium" | "high"
    ceiling_concurrency: int = 0
    first_429_concurrency: int | None = None
    sustain_success_rate_pct: float = 0.0
    sustain_duration_s: float = 0.0


def extract_header_limits(results: list[ChatResult]) -> tuple[float | None, float | None]:
    """Scan results for x-ratelimit-limit-requests / x-ratelimit-limit-tokens headers."""
    rpm_limit: float | None = None
    tpm_limit: float | None = None
    for r in results:
        h = r.rate_limit_headers
        if not h:
            continue
        val = h.get("x-ratelimit-limit-requests")
        if val:
            try:
                v = float(val)
                if rpm_limit is None or v > rpm_limit:
                    rpm_limit = v
            except ValueError:
                pass
        val = h.get("x-ratelimit-limit-tokens")
        if val:
            try:
                v = float(val)
                if tpm_limit is None or v > tpm_limit:
                    tpm_limit = v
            except ValueError:
                pass
    return rpm_limit, tpm_limit


def classify_limit_type(
    header_rpm: float | None, header_tpm: float | None
) -> str:
    if header_rpm and header_tpm:
        return "both"
    if header_rpm:
        return "rpm"
    if header_tpm:
        return "tpm"
    return "unknown"
