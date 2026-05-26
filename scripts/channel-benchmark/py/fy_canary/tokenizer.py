"""Tokenizer fingerprint probe — stateless prompt_tokens range validation."""

from __future__ import annotations

from dataclasses import dataclass

from .client import CanaryResponse
from .tokenizer_fingerprints import TOKENIZER_FINGERPRINTS


@dataclass
class TokenizerVerdict:
    prompt_id: str
    passed: bool
    deviation: float
    detail: str
    actual_tokens: int
    expected_range: tuple[int, int]


def evaluate_tokenizer(
    *,
    prompt_id: str,
    resp: CanaryResponse,
    expected_model: str,
    prompt_text: str,
    row_expected_range: list | None = None,
) -> TokenizerVerdict:
    if resp.status_code != 200 or resp.error:
        return TokenizerVerdict(
            prompt_id, False, 1.0,
            f"HTTP {resp.status_code}: {resp.error or 'unknown'}",
            actual_tokens=0, expected_range=(0, 0),
        )

    usage = resp.raw.get("usage", {})
    actual = usage.get("prompt_tokens")
    if actual is None:
        return TokenizerVerdict(
            prompt_id, False, 1.0, "prompt_tokens missing in usage",
            actual_tokens=0, expected_range=(0, 0),
        )

    expected_range: tuple[int, int] | None = None

    # Model-specific fingerprint takes priority (calibrated per tokenizer).
    fingerprints = TOKENIZER_FINGERPRINTS.get(expected_model, [])
    for text, lo, hi in fingerprints:
        if text == prompt_text:
            expected_range = (lo, hi)
            break

    # Fall back to row-level range (generic, cross-model).
    if expected_range is None and row_expected_range and isinstance(row_expected_range, list) and len(row_expected_range) == 2:
        expected_range = (int(row_expected_range[0]), int(row_expected_range[1]))

    if expected_range is None:
        return TokenizerVerdict(
            prompt_id, True, 0.0,
            f"no fingerprint for '{expected_model}' with prompt '{prompt_text[:30]}...', skip",
            actual_tokens=actual, expected_range=(0, 0),
        )

    lo, hi = expected_range
    if lo <= actual <= hi:
        return TokenizerVerdict(
            prompt_id, True, 0.0,
            f"prompt_tokens={actual} in [{lo},{hi}]",
            actual_tokens=actual, expected_range=expected_range,
        )

    mid = (lo + hi) / 2
    deviation = abs(actual - mid) / mid if mid > 0 else 1.0
    return TokenizerVerdict(
        prompt_id, False, deviation,
        f"prompt_tokens={actual} out of [{lo},{hi}] (deviation={deviation:.0%})",
        actual_tokens=actual, expected_range=expected_range,
    )
