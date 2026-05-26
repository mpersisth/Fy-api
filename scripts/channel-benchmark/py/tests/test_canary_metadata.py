"""Unit tests for metadata and tokenizer stateless probes."""

from fy_canary.client import CanaryResponse
from fy_canary.metadata import evaluate_metadata, _model_matches
from fy_canary.tokenizer import evaluate_tokenizer


class TestModelMatches:
    def test_exact_match(self):
        assert _model_matches("gpt-4o", "gpt-4o")

    def test_date_suffix(self):
        assert _model_matches("gpt-4o", "gpt-4o-2024-08-06")

    def test_preview_suffix(self):
        assert _model_matches("gpt-4o", "gpt-4o-preview")

    def test_latest_suffix(self):
        assert _model_matches("gpt-4o", "gpt-4o-latest")

    def test_rejects_mini(self):
        assert not _model_matches("gpt-4o", "gpt-4o-mini")

    def test_rejects_different_model(self):
        assert not _model_matches("gpt-4o", "claude-sonnet-4-6")

    def test_rejects_partial(self):
        assert not _model_matches("gpt-4", "gpt-4o")


class TestMetadataProbe:
    def _ok_resp(self, model="gpt-4o", pt=10, ct=5, fr="stop"):
        return CanaryResponse(
            content="answer",
            raw={
                "model": model,
                "usage": {"prompt_tokens": pt, "completion_tokens": ct},
                "choices": [{"message": {"role": "assistant", "content": "answer"}, "finish_reason": fr}],
            },
            status_code=200,
        )

    def test_all_pass(self):
        v = evaluate_metadata(prompt_id="t1", resp=self._ok_resp(), expected_model="gpt-4o", max_tokens=32)
        assert v.passed
        assert all(v.checks.values())

    def test_model_mismatch(self):
        v = evaluate_metadata(prompt_id="t2", resp=self._ok_resp(model="gpt-4o-mini"), expected_model="gpt-4o", max_tokens=32)
        assert not v.passed
        assert "model mismatch" in v.detail

    def test_http_error(self):
        resp = CanaryResponse(content="", raw={}, status_code=503, error="bad gateway")
        v = evaluate_metadata(prompt_id="t3", resp=resp, expected_model="gpt-4o")
        assert not v.passed
        assert "503" in v.detail

    def test_usage_missing(self):
        resp = CanaryResponse(
            content="x", raw={"model": "gpt-4o", "usage": {}, "choices": [{"message": {"role": "assistant", "content": "x"}, "finish_reason": "stop"}]},
            status_code=200,
        )
        v = evaluate_metadata(prompt_id="t4", resp=resp, expected_model="gpt-4o")
        assert not v.passed
        assert "usage" in v.detail

    def test_completion_tokens_exceeded(self):
        v = evaluate_metadata(prompt_id="t5", resp=self._ok_resp(ct=100), expected_model="gpt-4o", max_tokens=32)
        assert not v.passed
        assert "completion_tokens=100" in v.detail

    def test_reasoning_tokens_subtracted(self):
        resp = CanaryResponse(
            content="answer",
            raw={
                "model": "deepseek-v4-pro",
                "usage": {"prompt_tokens": 10, "completion_tokens": 55,
                          "completion_tokens_details": {"reasoning_tokens": 24}},
                "choices": [{"message": {"role": "assistant", "content": "answer"}, "finish_reason": "stop"}],
            },
            status_code=200,
        )
        v = evaluate_metadata(prompt_id="t-reason", resp=resp, expected_model="deepseek-v4-pro", max_tokens=32)
        assert v.passed, f"should pass: visible=55-24=31 <= 32, got: {v.detail}"

    def test_reasoning_tokens_still_fails_if_visible_exceeds(self):
        resp = CanaryResponse(
            content="answer",
            raw={
                "model": "deepseek-v4-pro",
                "usage": {"prompt_tokens": 10, "completion_tokens": 100,
                          "completion_tokens_details": {"reasoning_tokens": 24}},
                "choices": [{"message": {"role": "assistant", "content": "answer"}, "finish_reason": "stop"}],
            },
            status_code=200,
        )
        v = evaluate_metadata(prompt_id="t-reason2", resp=resp, expected_model="deepseek-v4-pro", max_tokens=32)
        assert not v.passed, f"should fail: visible=100-24=76 > 32"
        v = evaluate_metadata(prompt_id="t6", resp=self._ok_resp(fr="error"), expected_model="gpt-4o", max_tokens=32)
        assert not v.passed
        assert "finish_reason" in v.detail

    def test_null_finish_reason(self):
        resp = CanaryResponse(
            content="x",
            raw={"model": "gpt-4o", "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "choices": [{"message": {"role": "assistant", "content": "x"}, "finish_reason": None}]},
            status_code=200,
        )
        v = evaluate_metadata(prompt_id="t7", resp=resp, expected_model="gpt-4o", max_tokens=32)
        assert not v.passed

    def test_empty_content(self):
        resp = CanaryResponse(
            content="",
            raw={"model": "gpt-4o", "usage": {"prompt_tokens": 10, "completion_tokens": 0}, "choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}]},
            status_code=200,
        )
        v = evaluate_metadata(prompt_id="t8", resp=resp, expected_model="gpt-4o", max_tokens=32)
        assert not v.passed
        assert "empty content" in v.detail

    def test_date_suffix_passes(self):
        v = evaluate_metadata(prompt_id="t9", resp=self._ok_resp(model="gpt-4o-2024-08-06"), expected_model="gpt-4o", max_tokens=32)
        assert v.passed


class TestTokenizerProbe:
    def _resp(self, pt):
        return CanaryResponse(
            content="x", raw={"usage": {"prompt_tokens": pt, "completion_tokens": 1}}, status_code=200,
        )

    def test_in_range_row_level(self):
        v = evaluate_tokenizer(prompt_id="t1", resp=self._resp(10), expected_model="gpt-4o", prompt_text="anything", row_expected_range=[7, 14])
        assert v.passed

    def test_out_of_range(self):
        v = evaluate_tokenizer(prompt_id="t2", resp=self._resp(25), expected_model="gpt-4o", prompt_text="anything", row_expected_range=[7, 14])
        assert not v.passed
        assert "out of" in v.detail

    def test_no_fingerprint_degrades(self):
        v = evaluate_tokenizer(prompt_id="t3", resp=self._resp(10), expected_model="unknown-model-xyz", prompt_text="random text")
        assert v.passed
        assert "no fingerprint" in v.detail

    def test_fingerprint_library_hit(self):
        v = evaluate_tokenizer(prompt_id="t4", resp=self._resp(9), expected_model="gpt-4o", prompt_text="Hello, world!")
        assert v.passed

    def test_fingerprint_library_miss_text(self):
        v = evaluate_tokenizer(prompt_id="t5", resp=self._resp(9), expected_model="gpt-4o", prompt_text="not in library")
        assert v.passed
        assert "no fingerprint" in v.detail

    def test_model_fingerprint_overrides_row_range(self):
        # DeepSeek tokenizer is more efficient on CJK: actual=6 is in
        # model fingerprint [5,12] but outside row range [8,18].
        # Model fingerprint should take priority.
        v = evaluate_tokenizer(
            prompt_id="t-priority", resp=self._resp(6),
            expected_model="deepseek-v4-pro",
            prompt_text="人工智能改变了世界",
            row_expected_range=[8, 18],
        )
        assert v.passed, f"model fingerprint [5,12] should override row [8,18]: {v.detail}"

    def test_http_error(self):
        resp = CanaryResponse(content="", raw={}, status_code=0, error="connection refused")
        v = evaluate_tokenizer(prompt_id="t6", resp=resp, expected_model="gpt-4o", prompt_text="x")
        assert not v.passed

    def test_prompt_tokens_missing(self):
        resp = CanaryResponse(content="x", raw={"usage": {}}, status_code=200)
        v = evaluate_tokenizer(prompt_id="t7", resp=resp, expected_model="gpt-4o", prompt_text="x")
        assert not v.passed
        assert "missing" in v.detail
