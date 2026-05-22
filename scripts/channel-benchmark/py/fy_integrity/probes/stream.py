"""P2: Stream repackaging probe.

Analyzes inter-chunk timing to detect buffering/repackaging by middlemen.
Direct connections deliver chunks smoothly; repackagers show bursty patterns.
"""

from __future__ import annotations

import statistics

from .base import BaseProbe, ProbeResult


class StreamRepackagingProbe(BaseProbe):
    name = "stream_repackaging"
    severity = "warning"

    async def run(self, client, config) -> ProbeResult:
        rounds = config.probes.stream.rounds
        burst_threshold = config.probes.stream.burst_threshold
        model = config.target.model
        max_tokens = config.target.max_tokens

        prompt = "Write a short paragraph about the weather today."
        messages = [{"role": "user", "content": prompt}]

        all_burst_ratios: list[float] = []
        all_cvs: list[float] = []
        evidence: list[dict] = []

        for i in range(rounds):
            result = await client.stream_with_timing(
                model=model, messages=messages, max_tokens=max_tokens
            )
            if not result.success:
                evidence.append({"round": i, "error": result.error})
                continue
            if len(result.chunks) < 5:
                evidence.append(
                    {"round": i, "skipped": "too few chunks", "chunks": len(result.chunks)}
                )
                continue

            gaps = _inter_chunk_gaps(result.chunks)
            burst_ratio = _burst_ratio(gaps)
            cv = _coefficient_of_variation(gaps)
            all_burst_ratios.append(burst_ratio)
            all_cvs.append(cv)
            evidence.append({
                "round": i,
                "chunks": len(result.chunks),
                "burst_ratio": round(burst_ratio, 3),
                "cv": round(cv, 3),
                "gap_p50_ms": round(statistics.median(gaps) * 1000, 1),
                "gap_p95_ms": round(sorted(gaps)[int(len(gaps) * 0.95)] * 1000, 1),
            })

        if not all_burst_ratios:
            return self.skip_result("no successful streaming rounds")

        avg_burst = statistics.mean(all_burst_ratios)
        avg_cv = statistics.mean(all_cvs)

        if avg_burst > burst_threshold:
            return self.fail_result(
                f"burst_ratio={avg_burst:.0%} > threshold={burst_threshold:.0%} "
                f"(cv={avg_cv:.2f}) — stream repackaging likely",
                evidence=evidence,
                avg_burst_ratio=round(avg_burst, 3),
                avg_cv=round(avg_cv, 3),
                threshold=burst_threshold,
            )

        return self.pass_result(
            f"burst_ratio={avg_burst:.0%} (threshold={burst_threshold:.0%})",
            avg_burst_ratio=round(avg_burst, 3),
            avg_cv=round(avg_cv, 3),
        )


def _inter_chunk_gaps(chunks) -> list[float]:
    """Compute time gaps between consecutive chunks in seconds."""
    gaps = []
    for i in range(1, len(chunks)):
        gaps.append(chunks[i].timestamp - chunks[i - 1].timestamp)
    return gaps


def _burst_ratio(gaps: list[float], burst_window: float = 0.010) -> float:
    """Fraction of gaps that are < burst_window (10ms default)."""
    if not gaps:
        return 0.0
    bursts = sum(1 for g in gaps if g < burst_window)
    return bursts / len(gaps)


def _coefficient_of_variation(gaps: list[float]) -> float:
    if len(gaps) < 2:
        return 0.0
    mean = statistics.mean(gaps)
    if mean == 0:
        return 0.0
    return statistics.stdev(gaps) / mean
