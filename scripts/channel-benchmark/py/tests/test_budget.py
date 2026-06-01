"""Tests for BudgetTracker in fy_image_conformance."""

from fy_image_conformance.budget import BudgetTracker


def test_record_accumulates():
    tracker = BudgetTracker(max_cost_usd=10.0, default_cost=0.04)
    tracker.record("step1", 10)
    assert abs(tracker.total_spent - 0.40) < 1e-6
    tracker.record("step2", 5, 0.10)
    assert abs(tracker.total_spent - 0.90) < 1e-6


def test_would_exceed_true():
    tracker = BudgetTracker(max_cost_usd=1.0, default_cost=0.04)
    tracker.record("step1", 20)  # 0.80
    assert tracker.would_exceed(0.30) is True
    assert tracker.would_exceed(0.20) is False


def test_would_exceed_no_limit():
    tracker = BudgetTracker(max_cost_usd=None, default_cost=0.04)
    tracker.record("step1", 1000)
    assert tracker.would_exceed(999.0) is False


def test_should_warn():
    tracker = BudgetTracker(warn_cost_usd=0.50, default_cost=0.04)
    tracker.record("step1", 10)  # 0.40
    assert tracker.should_warn() is False
    tracker.record("step2", 5)  # 0.60
    assert tracker.should_warn() is True


def test_remaining():
    tracker = BudgetTracker(max_cost_usd=5.0, default_cost=0.04)
    tracker.record("step1", 25)  # 1.0
    assert abs(tracker.remaining - 4.0) < 1e-6


def test_remaining_none():
    tracker = BudgetTracker(max_cost_usd=None)
    assert tracker.remaining is None


def test_summary_format():
    tracker = BudgetTracker(max_cost_usd=10.0, default_cost=0.04)
    tracker.record("API兼容性", 5)
    tracker.record("安全测试", 8)
    summary = tracker.summary()
    assert "API兼容性" in summary
    assert "安全测试" in summary
    assert "$" in summary
