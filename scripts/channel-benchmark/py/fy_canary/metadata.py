"""Metadata probe — stateless validation of API response fields."""

from __future__ import annotations

from dataclasses import dataclass, field

from .client import CanaryResponse


_DEFAULT_ALLOWED_FINISH_REASONS = {"stop", "length", "content_filter"}


@dataclass
class MetadataVerdict:
    prompt_id: str
    passed: bool
    detail: str
    checks: dict[str, bool] = field(default_factory=dict)


def _model_matches(expected: str, actual: str) -> bool:
    """Allow date-suffix variants (gpt-4o-2024-08-06) but reject
    different models (gpt-4o-mini). Uses prefix + hyphen-digit check
    rather than naive substring to avoid false positives."""
    if actual == expected:
        return True
    if not actual.startswith(expected):
        return False
    suffix = actual[len(expected):]
    # Allow: "-2024-08-06", "-20240806", "-preview", "-latest"
    # Reject: "-mini", "-micro" (these start with "-m" not "-2"/"-p"/"-l")
    return suffix.startswith(("-20", "-preview", "-latest"))


def evaluate_metadata(
    *,
    prompt_id: str,
    resp: CanaryResponse,
    expected_model: str,
    max_tokens: int = 200,
    allowed_finish_reasons: set[str] | None = None,
) -> MetadataVerdict:
    if allowed_finish_reasons is None:
        allowed_finish_reasons = _DEFAULT_ALLOWED_FINISH_REASONS

    checks: dict[str, bool] = {}

    if resp.status_code != 200 or resp.error:
        return MetadataVerdict(
            prompt_id=prompt_id, passed=False,
            detail=f"HTTP {resp.status_code}: {resp.error or 'unknown'}",
            checks=checks,
        )

    raw = resp.raw
    expected = expected_model.lower()
    actual = (raw.get("model") or "").lower()
    checks["model"] = _model_matches(expected, actual)
    if not checks["model"]:
        return MetadataVerdict(
            prompt_id, False,
            f"model mismatch: got '{actual}', expected '{expected}'", checks,
        )

    usage = raw.get("usage", {})
    checks["usage"] = usage.get("prompt_tokens", 0) > 0
    if not checks["usage"]:
        return MetadataVerdict(
            prompt_id, False, "usage field missing or prompt_tokens=0", checks,
        )

    ct = usage.get("completion_tokens", 0)
    # Reasoning models (DeepSeek, o1, etc.) include reasoning_tokens in
    # completion_tokens. Subtract them for the max_tokens check since
    # max_tokens only constrains visible output, not internal reasoning.
    details = usage.get("completion_tokens_details") or {}
    reasoning = details.get("reasoning_tokens", 0)
    visible_ct = ct - reasoning
    checks["tokens"] = visible_ct <= max_tokens
    if not checks["tokens"]:
        return MetadataVerdict(
            prompt_id, False,
            f"completion_tokens={ct} (visible={visible_ct}, reasoning={reasoning}) > max_tokens={max_tokens}",
            checks,
        )

    choices = raw.get("choices", [{}])
    fr = choices[0].get("finish_reason") if choices else None
    checks["finish"] = fr in allowed_finish_reasons
    if not checks["finish"]:
        return MetadataVerdict(
            prompt_id, False, f"unexpected finish_reason: '{fr}'", checks,
        )

    msg = (choices[0].get("message", {}) if choices else {})
    checks["role"] = msg.get("role") == "assistant"
    if not checks["role"]:
        return MetadataVerdict(
            prompt_id, False, f"unexpected role: '{msg.get('role')}'", checks,
        )

    checks["content"] = len(resp.content) > 0
    if not checks["content"]:
        return MetadataVerdict(prompt_id, False, "empty content in response", checks)

    return MetadataVerdict(
        prompt_id, True,
        f"model OK usage OK tokens OK finish={fr} role OK content OK", checks,
    )
