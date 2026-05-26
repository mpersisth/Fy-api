"""Test cache ratio aggregation across repetitions."""
import pytest
from fy_cache_affinity.metrics import aggregate_runs
from fy_cache_affinity.conversation import TurnResult, ConversationResult


def _make_run(ratios: list[float]) -> ConversationResult:
    turns = [
        TurnResult(turn=i+1, prompt_tokens=100*(i+1), cached_tokens=int(100*(i+1)*r), ttft_ms=200, e2e_ms=1000)
        for i, r in enumerate(ratios)
    ]
    return ConversationResult(seed="test", session_id="abc", turns=turns)


def test_aggregate_runs_averages():
    runs = [
        _make_run([0.0, 0.5, 0.8]),
        _make_run([0.0, 0.4, 0.7]),
        _make_run([0.0, 0.6, 0.9]),
    ]
    agg = aggregate_runs(runs)
    assert len(agg) == 3
    assert agg[0].avg_cache_ratio == pytest.approx(0.0)
    assert agg[1].avg_cache_ratio == pytest.approx(0.5)
    assert agg[2].avg_cache_ratio == pytest.approx(0.8)
    assert agg[2].min_cache_ratio == pytest.approx(0.7)
    assert agg[2].max_cache_ratio == pytest.approx(0.9)


def test_aggregate_runs_uneven_lengths():
    runs = [
        _make_run([0.0, 0.5, 0.8]),
        _make_run([0.0, 0.4]),
    ]
    agg = aggregate_runs(runs)
    assert len(agg) == 3
    assert agg[2].avg_cache_ratio == pytest.approx(0.8)
    assert agg[2].samples == 1


def test_aggregate_runs_empty():
    assert aggregate_runs([]) == []
