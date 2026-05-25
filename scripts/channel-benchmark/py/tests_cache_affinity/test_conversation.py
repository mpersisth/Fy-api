"""Test multi-turn conversation driver."""
import pytest
from fy_cache_affinity.conversation import TurnResult, build_followup_messages, build_seed_messages


def test_turn_result_cache_ratio():
    t = TurnResult(turn=2, prompt_tokens=200, cached_tokens=100, ttft_ms=300, e2e_ms=1200)
    assert t.cache_ratio == pytest.approx(0.5)


def test_turn_result_cache_ratio_zero_prompt():
    t = TurnResult(turn=1, prompt_tokens=0, cached_tokens=0, ttft_ms=0, e2e_ms=0)
    assert t.cache_ratio == 0.0


def test_build_followup_messages():
    history = [
        {"role": "user", "content": "What is Go?"},
        {"role": "assistant", "content": "Go is a programming language."},
    ]
    msgs = build_followup_messages(history)
    assert len(msgs) == 3
    assert msgs[-1]["role"] == "user"
    assert "深入" in msgs[-1]["content"] or "问题" in msgs[-1]["content"]


def test_build_seed_messages():
    msgs = build_seed_messages("Go concurrency")
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert "Go concurrency" in msgs[0]["content"]
