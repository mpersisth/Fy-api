"""Tests for the canary verdict false-positive fix.

When all capability probes fail due to infrastructure errors (judge 503,
generation 429), the verdict should be INCONCLUSIVE, not MISMATCH.
"""

from fy_image_canary.verdict import ProbeOutcome, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW
from fy_image_canary.runner import ImageCanaryRunner
from fy_image_canary.config import (
    ImageCanaryConfig, GatewayTarget, JudgeConfig,
    FingerprintConfig, ThresholdConfig, BudgetConfig,
)


def _make_cfg():
    return ImageCanaryConfig(
        gateway=GatewayTarget(
            name="test", base_url="http://localhost:3000",
            user_token="sk-test", pin_channel_id=1, model="gpt-image-2"),
        judge=JudgeConfig(),
        fingerprint=FingerprintConfig(),
        thresholds=ThresholdConfig(),
        budget=BudgetConfig(),
    )


def test_all_capability_probes_failed_returns_inconclusive():
    """When ONLY capability probes exist and all have score=0 (judge error), verdict = INCONCLUSIVE."""
    runner = ImageCanaryRunner(_make_cfg())
    outcomes = [
        ProbeOutcome("5b3-1", "capability", False, 0.0, "judge API error: 503", ""),
        ProbeOutcome("5b3-2", "capability", False, 0.0, "judge API error: 503", ""),
        ProbeOutcome("5b3-3", "capability", False, 0.0, "judge API error: 503", ""),
        ProbeOutcome("5b3-4", "capability", False, 0.0, "generation failed: 429", ""),
    ]
    verdict, confidence = runner._compute_combined_verdict(outcomes)
    assert verdict == "INCONCLUSIVE"
    assert confidence == "low"


def test_valid_probes_pass_even_when_capability_all_fail():
    """When fingerprint/xc pass but all capability probes are invalid, verdict = PASS."""
    runner = ImageCanaryRunner(_make_cfg())
    outcomes = [
        ProbeOutcome("5b1", "fingerprint", True, 0.5, "no fingerprint data", CONFIDENCE_LOW),
        ProbeOutcome("5b2", "cross_channel", True, 0.5, "CLIP not available", CONFIDENCE_LOW),
        ProbeOutcome("5b3-1", "capability", False, 0.0, "judge API error: 503", ""),
        ProbeOutcome("5b3-2", "capability", False, 0.0, "judge API error: 503", ""),
        ProbeOutcome("5b3-3", "capability", False, 0.0, "generation failed: 429", ""),
    ]
    verdict, confidence = runner._compute_combined_verdict(outcomes)
    # Fingerprint + cross_channel passed → valid pass_rate = 2/2 = 100%
    assert verdict == "PASS"


def test_mixed_capability_probes_uses_valid_only():
    """When some capability probes succeed and some fail, only valid ones count."""
    runner = ImageCanaryRunner(_make_cfg())
    outcomes = [
        ProbeOutcome("5b1", "fingerprint", True, 0.5, "no data", CONFIDENCE_LOW),
        ProbeOutcome("5b2", "cross_channel", True, 0.5, "CLIP skip", CONFIDENCE_LOW),
        ProbeOutcome("5b3-1", "capability", True, 1.0, "perfect", CONFIDENCE_MEDIUM),
        ProbeOutcome("5b3-2", "capability", True, 1.0, "perfect", CONFIDENCE_MEDIUM),
        ProbeOutcome("5b3-3", "capability", True, 1.0, "perfect", CONFIDENCE_MEDIUM),
        ProbeOutcome("5b3-4", "capability", False, 0.0, "judge API error: 503", ""),
        ProbeOutcome("5b3-5", "capability", False, 0.0, "generation failed: 429", ""),
    ]
    verdict, confidence = runner._compute_combined_verdict(outcomes)
    # 5 valid outcomes (2 fingerprint/xc + 3 capability passed), all passed
    assert verdict == "PASS"


def test_real_capability_failure_still_triggers_mismatch():
    """A genuine capability failure (score > 0 but low) still counts."""
    runner = ImageCanaryRunner(_make_cfg())
    outcomes = [
        ProbeOutcome("5b3-1", "capability", False, 0.2, "poor quality", CONFIDENCE_MEDIUM),
        ProbeOutcome("5b3-2", "capability", False, 0.1, "poor quality", CONFIDENCE_MEDIUM),
        ProbeOutcome("5b3-3", "capability", False, 0.3, "mediocre", CONFIDENCE_MEDIUM),
    ]
    verdict, confidence = runner._compute_combined_verdict(outcomes)
    # These are real failures (score > 0), should still count
    assert verdict == "MISMATCH"
    assert confidence == "medium"
