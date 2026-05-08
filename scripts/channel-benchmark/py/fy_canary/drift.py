"""Embedding-centroid drift probe.

Generate N outputs per prompt, embed each, compute the centroid. Compare
the audit-time centroid to the baseline centroid via cosine. A drop
below threshold indicates the output distribution has shifted — either
because the model was swapped or because the provider pushed an update.

This probe is more sensitive than alignment (catches same-family
substitutions) but less rigorous than MMD (no calibrated p-value).
It's a good middle ground when you don't want to carry torch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class DriftVerdict:
    prompt_id: str
    similarity: float      # centroid cosine, 1.0 = identical centroids
    threshold: float
    passed: bool
    n_samples: int


def centroid(vectors: list[list[float]]) -> list[float]:
    """Element-wise mean of N equal-length vectors. Returns [] on empty input."""
    if not vectors:
        return []
    dim = len(vectors[0])
    out = [0.0] * dim
    for v in vectors:
        if len(v) != dim:
            raise ValueError(f"vector length mismatch: got {len(v)}, expected {dim}")
        for i, x in enumerate(v):
            out[i] += x
    n = float(len(vectors))
    return [x / n for x in out]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def evaluate_drift(
    *,
    prompt_id: str,
    baseline_centroid: list[float],
    current_centroid: list[float],
    n_samples: int,
    threshold: float = 0.93,
) -> DriftVerdict:
    sim = cosine(baseline_centroid, current_centroid)
    return DriftVerdict(
        prompt_id=prompt_id,
        similarity=sim,
        threshold=threshold,
        passed=sim >= threshold,
        n_samples=n_samples,
    )
