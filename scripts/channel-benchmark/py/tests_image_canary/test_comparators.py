"""Unit tests for image canary comparators."""

import math


def test_cosine_identical():
    from fy_image_canary.comparators.clip import _cosine
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert abs(_cosine(a, b) - 1.0) < 1e-6


def test_cosine_orthogonal():
    from fy_image_canary.comparators.clip import _cosine
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(_cosine(a, b)) < 1e-6


def test_cosine_opposite():
    from fy_image_canary.comparators.clip import _cosine
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert abs(_cosine(a, b) - (-1.0)) < 1e-6


def test_cosine_zero_vector():
    from fy_image_canary.comparators.clip import _cosine
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_clip_available_returns_bool():
    from fy_image_canary.comparators.clip import clip_available
    result = clip_available()
    assert isinstance(result, bool)


def test_pillow_available_returns_bool():
    from fy_image_canary.comparators.histogram import pillow_available
    result = pillow_available()
    assert isinstance(result, bool)


def test_pearson_correlation_identical():
    from fy_image_canary.comparators.histogram import _pearson_correlation
    a = [0.1, 0.2, 0.3, 0.4]
    assert abs(_pearson_correlation(a, a) - 1.0) < 1e-6


def test_pearson_correlation_anticorrelated():
    from fy_image_canary.comparators.histogram import _pearson_correlation
    a = [1.0, 2.0, 3.0, 4.0]
    b = [4.0, 3.0, 2.0, 1.0]
    assert abs(_pearson_correlation(a, b) - (-1.0)) < 1e-6


def test_vlm_parse_verdict():
    from fy_image_canary.comparators.vlm_judge import _parse_verdict
    verdict, reasoning = _parse_verdict('{"verdict": "same", "reasoning": "similar style"}')
    assert verdict == "same"
    assert "similar" in reasoning


def test_vlm_parse_verdict_with_markdown():
    from fy_image_canary.comparators.vlm_judge import _parse_verdict
    text = '```json\n{"verdict": "different", "reasoning": "distinct styles"}\n```'
    verdict, reasoning = _parse_verdict(text)
    assert verdict == "different"


def test_vlm_parse_verdict_fallback():
    from fy_image_canary.comparators.vlm_judge import _parse_verdict
    verdict, _ = _parse_verdict("These images look the same to me.")
    assert verdict == "same"
