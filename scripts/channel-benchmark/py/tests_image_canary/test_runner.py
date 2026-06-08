"""Integration tests for runner verdict computation."""

from fy_image_canary.verdict import ProbeOutcome, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW
from fy_image_canary.runner import ImageCanaryRunner
from fy_image_canary.config import ImageCanaryConfig, GatewayTarget, JudgeConfig, FingerprintConfig, ThresholdConfig, BudgetConfig


def _make_cfg():
    return ImageCanaryConfig(
        gateway=GatewayTarget(
            name="test", base_url="http://localhost:3000",
            user_token="sk-test", pin_channel_id=1, model="dall-e-3"),
        judge=JudgeConfig(),
        fingerprint=FingerprintConfig(),
        thresholds=ThresholdConfig(),
        budget=BudgetConfig(),
    )


def test_combined_verdict_all_pass():
    runner = ImageCanaryRunner(_make_cfg())
    outcomes = [
        ProbeOutcome("p1", "clip", True, 0.95, "ok", CONFIDENCE_HIGH),
        ProbeOutcome("p2", "color_histogram", True, 0.90, "ok", CONFIDENCE_HIGH),
        ProbeOutcome("p3", "vlm_comparison", True, 1.0, "same", CONFIDENCE_HIGH),
    ]
    verdict, confidence = runner._compute_combined_verdict(outcomes)
    assert verdict == "PASS"
    assert confidence == "high"


def test_combined_verdict_hard_fail():
    runner = ImageCanaryRunner(_make_cfg())
    outcomes = [
        ProbeOutcome("p1", "clip", False, 0.5, "low similarity", CONFIDENCE_HIGH),
        ProbeOutcome("p2", "fingerprint", True, 1.0, "ok", CONFIDENCE_MEDIUM),
    ]
    verdict, confidence = runner._compute_combined_verdict(outcomes)
    assert verdict == "MISMATCH"


def test_combined_verdict_fingerprint_fail():
    runner = ImageCanaryRunner(_make_cfg())
    outcomes = [
        ProbeOutcome("p1", "fingerprint", False, 0.0,
                     "model accepted unsupported param — likely NOT dall-e-3", "high"),
        ProbeOutcome("p2", "fingerprint", True, 1.0, "ok", CONFIDENCE_MEDIUM),
    ]
    verdict, confidence = runner._compute_combined_verdict(outcomes)
    assert verdict == "MISMATCH"


def test_combined_verdict_inconclusive():
    runner = ImageCanaryRunner(_make_cfg())
    outcomes = [
        ProbeOutcome("p1", "fingerprint", True, 1.0, "ok", CONFIDENCE_MEDIUM),
        ProbeOutcome("p2", "fingerprint", False, 0.0, "speed mismatch", CONFIDENCE_MEDIUM),
        ProbeOutcome("p3", "capability", True, 0.8, "ok", CONFIDENCE_MEDIUM),
        ProbeOutcome("p4", "capability", False, 0.3, "bad", CONFIDENCE_MEDIUM),
    ]
    verdict, confidence = runner._compute_combined_verdict(outcomes)
    assert verdict == "INCONCLUSIVE"


def test_combined_verdict_empty():
    runner = ImageCanaryRunner(_make_cfg())
    verdict, confidence = runner._compute_combined_verdict([])
    assert verdict == "INCONCLUSIVE"
    assert confidence == "low"
