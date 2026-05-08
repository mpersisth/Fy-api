"""Alignment / refusal-template fingerprint probe.

The intuition: different model families refuse (and accept) things in
characteristic, model-specific ways. GPT-4 says "I can't help with that but
I can help you think through..." Claude says "I won't be able to...",
Gemini says "I'm not able to provide...". The EXACT phrasing and hedge
patterns differ enough that two samples from the same family at temp=0
cluster tightly, while a substitution shows up as a distance outlier.

This probe works by:
  1. At baseline time, store the exact response string for each alignment
     prompt.
  2. At audit time, re-ask the same prompt and compute a character-level
     similarity score (normalized Levenshtein).
  3. Fire when similarity drops below a threshold.

It's cheap — 1 request per prompt per audit — and catches sloppy
substitutions (different model family). It will NOT catch substitutions
within the same family (e.g. GPT-4o → GPT-4o-mini), which is what the MMD
and drift probes are for.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AlignmentVerdict:
    prompt_id: str
    similarity: float         # 0-1, 1.0 = byte-identical
    threshold: float
    passed: bool
    baseline_sample: str
    current_sample: str


def normalized_edit_similarity(a: str, b: str) -> float:
    """1 - (levenshtein(a,b) / max(len(a), len(b)))  — clamped to [0,1].

    Implemented with the straightforward DP; O(len(a)*len(b)) which is fine
    for alignment samples capped at ~200 tokens each.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    # Fast path: identical → skip the DP entirely.
    if a == b:
        return 1.0
    lev = _levenshtein(a, b)
    denom = max(len(a), len(b))
    return max(0.0, 1.0 - lev / denom)


def _levenshtein(a: str, b: str) -> int:
    """Iterative two-row DP. O(len(a) * len(b)) time, O(min) space."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            ins = curr[j - 1] + 1
            dele = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            curr[j] = min(ins, dele, sub)
        prev = curr
    return prev[-1]


def evaluate_alignment(
    *,
    prompt_id: str,
    baseline_sample: str,
    current_sample: str,
    threshold: float = 0.70,
) -> AlignmentVerdict:
    sim = normalized_edit_similarity(baseline_sample, current_sample)
    return AlignmentVerdict(
        prompt_id=prompt_id,
        similarity=sim,
        threshold=threshold,
        passed=sim >= threshold,
        baseline_sample=baseline_sample,
        current_sample=current_sample,
    )
