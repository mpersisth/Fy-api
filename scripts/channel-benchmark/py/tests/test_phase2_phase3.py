"""Tests for Phase 2+3: two-phase prompts, output_valid, image scorecard."""

from fy_image_conformance.suites.prompt_follow import (
    PHASE_A_PROMPTS, PHASE_B_PROMPTS, _stddev, PhaseResult, JudgeResult,
)
from fy_image_conformance.suites.output_valid import (
    _detect_format, _detect_dimensions, _webp_dimensions,
)
from fy_image_conformance.suites.safety import SAFETY_PROMPTS, _SAFETY_REJECT_CODES
from fy_score.scorer import (
    build_image_scorecard, score_image_performance, score_image_quality,
    score_image_authenticity, score_image_compliance,
    IMAGE_WEIGHTS, IMAGE_AVAILABILITY_GATE,
)


# --- Two-phase prompt tests ---

def test_phase_a_has_10_prompts():
    assert len(PHASE_A_PROMPTS) == 10


def test_phase_b_has_20_prompts():
    assert len(PHASE_B_PROMPTS) == 20


def test_phase_a_balanced_languages():
    zh = [p for p in PHASE_A_PROMPTS if p["lang"] == "zh"]
    en = [p for p in PHASE_A_PROMPTS if p["lang"] == "en"]
    assert len(zh) == 5
    assert len(en) == 5


def test_phase_b_balanced_languages():
    zh = [p for p in PHASE_B_PROMPTS if p["lang"] == "zh"]
    en = [p for p in PHASE_B_PROMPTS if p["lang"] == "en"]
    assert len(zh) == 10
    assert len(en) == 10


def test_high_variance_prompts_marked():
    hv_a = [p for p in PHASE_A_PROMPTS if p.get("high_variance")]
    hv_b = [p for p in PHASE_B_PROMPTS if p.get("high_variance")]
    assert len(hv_a) == 1  # A07_culture
    assert len(hv_b) == 3  # B01, B03, B05


def test_stddev():
    assert _stddev([1.0, 1.0, 1.0]) == 0.0
    assert abs(_stddev([0.0, 1.0]) - 0.5) < 1e-6
    assert _stddev([5.0]) == 0.0


def test_phase_result_weighted_pass_rate():
    pr = PhaseResult(phase="A", results=[
        JudgeResult("p1", 0.8, True, is_high_variance_prompt=False),
        JudgeResult("p2", 0.3, False, is_high_variance_prompt=False),
        JudgeResult("p3", 0.7, True, is_high_variance_prompt=True),  # weight 0.5
    ])
    # weights: 1.0 + 1.0 + 0.5 = 2.5
    # passed weighted: 1.0 + 0.5 = 1.5
    assert abs(pr.weighted_pass_rate - 0.6) < 1e-6


# --- Output validation tests ---

def test_detect_format_png():
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    assert _detect_format(png_header) == "png"


def test_detect_format_jpeg():
    jpeg_header = b"\xff\xd8" + b"\x00" * 100
    assert _detect_format(jpeg_header) == "jpeg"


def test_detect_format_webp():
    webp_header = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100
    assert _detect_format(webp_header) == "webp"


def test_detect_format_unknown():
    assert _detect_format(b"\x00\x00\x00\x00") == ""


def test_detect_png_dimensions():
    import struct
    # PNG: 8-byte magic + 4-byte length + 4-byte "IHDR" + 4-byte width + 4-byte height
    ihdr = b"\x89PNG\r\n\x1a\n"      # 8 bytes magic
    ihdr += b"\x00\x00\x00\x0d"      # 4 bytes IHDR chunk length
    ihdr += b"IHDR"                   # 4 bytes chunk type
    ihdr += struct.pack(">I", 1024)   # 4 bytes width  (offset 16)
    ihdr += struct.pack(">I", 768)    # 4 bytes height (offset 20)
    ihdr += b"\x00" * 20             # rest of IHDR + CRC padding
    assert _detect_dimensions(ihdr, "png") == (1024, 768)


# --- Safety tests ---

def test_safety_has_8_prompts():
    assert len(SAFETY_PROMPTS) == 8


def test_safety_categories_complete():
    categories = {p["category"] for p in SAFETY_PROMPTS}
    assert "NSFW" in categories
    assert "暴力" in categories
    assert "政治敏感" in categories
    assert "自残" in categories
    assert "仇恨言论" in categories
    assert "未成年人保护" in categories
    assert "擦边" in categories
    assert "正常对照" in categories


def test_safety_reject_codes():
    assert 400 in _SAFETY_REJECT_CODES
    assert 403 in _SAFETY_REJECT_CODES
    assert 451 in _SAFETY_REJECT_CODES
    assert 500 not in _SAFETY_REJECT_CODES


# --- Image scorecard tests ---

def test_image_weights_sum_to_1():
    assert abs(sum(IMAGE_WEIGHTS.values()) - 1.0) < 1e-6


def test_image_availability_gate():
    assert IMAGE_AVAILABILITY_GATE == 0.90


def test_build_image_scorecard_basic():
    card = build_image_scorecard(
        channel_name="test-ch", channel_id=1, model="dall-e-3",
        success_rate=0.95,
        p95_ms=10000, p50_ms=6000, rpm=5.0,
        zh_pass_rate=0.80, en_pass_rate=0.90, output_valid_rate=0.95,
        safety_pass_rate=1.0, api_compat_pass_rate=0.90,
        canary_pass_rate=0.9, canary_avg_score=0.85,
    )
    assert card.grade in ("A", "B", "C", "D", "F")
    assert card.composite_score > 0
    assert not card.gated_out


def test_build_image_scorecard_gated_out():
    card = build_image_scorecard(
        channel_name="bad-ch", channel_id=2, model="dall-e-3",
        success_rate=0.85,  # below 90% gate
    )
    assert card.gated_out
    assert card.composite_score == 0.0
    assert card.grade == "F"


def test_image_quality_phase_a_blocked():
    result = score_image_quality(phase_a_blocked=True)
    assert result.score == 0.0
    assert "Phase A blocked" in result.detail


def test_image_authenticity_mismatch():
    result = score_image_authenticity(
        canary_pass_rate=0.5, canary_avg_score=0.5, confidence_cap=0.0)
    assert result.score == 0.0
    assert "MISMATCH" in result.detail


def test_image_performance_scoring():
    result = score_image_performance(p95_ms=5000.0, rpm=10.0, p50_ms=5000.0)
    assert result.score == 100.0  # all at best values
    assert result.available


def test_image_performance_worst():
    result = score_image_performance(p95_ms=60000.0, rpm=1.0, p50_ms=20000.0)
    assert result.score == 0.0
