"""Tests for fy-conformance.

We split this into three layers:

1. Pure unit tests for the grader (no I/O, no asyncio).
2. Dataset loader tests (file → list[Case]).
3. Async runner test using httpx.MockTransport for end-to-end coverage of
   the fast-fail path that the gateway hot-fix in 2026-05 was built for.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import httpx
import pytest

from fy_conformance.config import Config, GatewayCfg, TargetCfg
from fy_conformance.dataset import Case, build_request_body, load as load_dataset
from fy_conformance.grader import Result, Verdict, grade, status_class
from fy_conformance.runner import aggregate, run_all, to_jsonl, to_markdown


# --------------------------------------------------------------------------
# Layer 1 — grader unit tests
# --------------------------------------------------------------------------

def _case(**kw) -> Case:
    defaults = dict(id="t", category="unit", endpoint="/v1/chat/completions", method="POST")
    defaults.update(kw)
    return Case(**defaults)


def test_status_class_buckets():
    assert status_class(200) == "2xx"
    assert status_class(204) == "2xx"
    assert status_class(400) == "4xx"
    assert status_class(404) == "4xx"
    assert status_class(500) == "5xx"
    assert status_class(503) == "5xx"


def test_grade_pass_when_class_matches():
    c = _case(expect_status_class="4xx", expect_message_contains=["max_tokens"])
    r = grade(c, 400, '{"error":{"message":"invalid max_tokens"}}')
    assert r.verdict is Verdict.PASS, r.reasons


def test_grade_fail_when_class_wrong():
    """The exact bug the gateway hot-fix addressed: 400 -> 500."""
    c = _case(expect_status_class="4xx", expect_message_contains=["max_tokens"])
    r = grade(c, 500, '{"error":{"message":"json: cannot unmarshal string into Go struct field GeneralOpenAIRequest.max_tokens of type uint"}}')
    assert r.verdict is Verdict.FAIL
    # Must catch BOTH problems: the 5xx miscategorization AND the leak.
    joined = " | ".join(r.reasons)
    assert "5xx" in joined
    # leak detection only fires if must_not_contain is set (case-by-case)
    c2 = _case(
        expect_status_class="4xx",
        must_not_contain=["Go struct field"],
    )
    r2 = grade(c2, 500, "json: cannot unmarshal string into Go struct field GeneralOpenAIRequest.max_tokens of type uint")
    assert r2.verdict is Verdict.FAIL
    assert any("Go struct field" in reason for reason in r2.reasons)


def test_grade_fail_when_message_missing():
    c = _case(expect_status_class="4xx", expect_message_contains=["max_tokens"])
    r = grade(c, 400, '{"error":{"message":"something else"}}')
    assert r.verdict is Verdict.FAIL


def test_grade_fail_when_leak_detected():
    c = _case(expect_status_class="5xx", must_not_contain=["GeneralOpenAIRequest"])
    r = grade(c, 500, "json: cannot unmarshal ... GeneralOpenAIRequest.max_tokens ...")
    assert r.verdict is Verdict.FAIL


def test_grade_status_class_or_alternative():
    """`4xx_or_5xx` accepts either."""
    c = _case(expect_status_class="4xx_or_5xx")
    assert grade(c, 404, "").verdict is Verdict.PASS
    assert grade(c, 503, "").verdict is Verdict.PASS
    assert grade(c, 200, "").verdict is Verdict.FAIL


def test_grade_exact_status_code():
    c = _case(expect_status_code=401)
    assert grade(c, 401, "").verdict is Verdict.PASS
    assert grade(c, 403, "").verdict is Verdict.FAIL


def test_grade_case_insensitive_substrings():
    c = _case(expect_status_class="4xx", expect_message_contains=["MAX_TOKENS"])
    assert grade(c, 400, "Invalid max_tokens value").verdict is Verdict.PASS


# --------------------------------------------------------------------------
# Layer 2 — dataset loader / request builder
# --------------------------------------------------------------------------

def test_load_dataset_round_trip(tmp_path: Path):
    p = tmp_path / "ds.jsonl"
    p.write_text(
        '{"id":"x1","category":"foo","override_field":"max_tokens","override_value":"abc","expect_status_class":"4xx"}\n'
        '\n'
        '# comment line\n'
        '{"id":"x2","raw_body":"{not json","expect_status_class":"4xx"}\n',
        encoding="utf-8",
    )
    cases = load_dataset(p)
    assert [c.id for c in cases] == ["x1", "x2"]
    assert cases[0].override_field == "max_tokens"
    assert cases[0].override_value == "abc"
    assert cases[0].has_override_value()
    assert cases[1].raw_body == "{not json"


def test_load_dataset_requires_id(tmp_path: Path):
    p = tmp_path / "ds.jsonl"
    p.write_text('{"category":"no-id"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="missing required 'id'"):
        load_dataset(p)


def test_build_request_body_override_field():
    baseline = {"model": "x", "max_tokens": 16}
    case = _case(override_field="max_tokens", override_value="abc")
    raw, body = build_request_body(case, baseline)
    assert raw is None
    assert body == {"model": "x", "max_tokens": "abc"}
    # baseline untouched (deep-copy)
    assert baseline["max_tokens"] == 16


def test_build_request_body_remove_field():
    baseline = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
    case = _case(remove_field="messages")
    raw, body = build_request_body(case, baseline)
    assert raw is None
    assert "messages" not in body
    assert "messages" in baseline


def test_build_request_body_raw():
    case = _case(raw_body='{"not":"json')
    raw, body = build_request_body(case, {"model": "x"})
    assert raw == '{"not":"json'
    assert body is None


def test_override_value_can_be_null():
    case = _case(override_field="model", override_value=None)
    assert case.has_override_value()
    raw, body = build_request_body(case, {"model": "x", "messages": []})
    assert body == {"model": None, "messages": []}


# --------------------------------------------------------------------------
# Layer 3 — full async runner with MockTransport
# --------------------------------------------------------------------------

def _make_transport(response_for: Callable[[httpx.Request], tuple[int, str]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        status, body = response_for(request)
        return httpx.Response(status, text=body)
    return httpx.MockTransport(handler)


def _make_cfg(tmp_path: Path) -> Config:
    return Config(
        gateway=GatewayCfg(base_url="http://mock", user_token="sk-test"),
        target=TargetCfg(model="m", baseline_request={"model": "m", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 16}),
        dataset=tmp_path / "unused.jsonl",
        output_dir=tmp_path / "out",
        concurrency=4,
        request_timeout_sec=5.0,
        extra_headers={},
    )


@pytest.mark.asyncio
async def test_runner_pre_fix_gateway_fails_conformance(tmp_path: Path, monkeypatch):
    """Simulate the 2026-05-pre-fix gateway behavior — 500 + Go struct leak —
    and check the runner catches it as FAIL."""
    cases = [
        Case(
            id="auto-max_tokens-bad-type",
            category="param_validation_auto",
            override_field="max_tokens",
            override_value="abc",
            expect_status_class="4xx",
            expect_message_contains=["max_tokens"],
            must_not_contain=["Go struct field"],
        ),
    ]

    def respond(req: httpx.Request) -> tuple[int, str]:
        return (
            500,
            json.dumps({
                "error": {
                    "message": "json: cannot unmarshal string into Go struct field GeneralOpenAIRequest.max_tokens of type uint",
                    "type": "new_api_error",
                    "code": "invalid_request",
                }
            }),
        )

    transport = _make_transport(respond)
    orig_init = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient, "__init__",
        lambda self, *a, **k: orig_init(self, *a, **{**k, "transport": transport}),
    )

    cfg = _make_cfg(tmp_path)
    results = await run_all(cfg, cases)
    assert len(results) == 1
    r = results[0]
    assert r.verdict is Verdict.FAIL, r.reasons
    joined = " | ".join(r.reasons)
    assert "5xx" in joined  # status class violation
    assert "Go struct field" in joined  # leak violation


@pytest.mark.asyncio
async def test_runner_post_fix_gateway_passes(tmp_path: Path, monkeypatch):
    """Simulate the post-fix gateway behavior — 400 + sanitized message —
    and check the runner reports PASS."""
    cases = [
        Case(
            id="auto-max_tokens-bad-type",
            category="param_validation_auto",
            override_field="max_tokens",
            override_value="abc",
            expect_status_class="4xx",
            expect_message_contains=["max_tokens"],
            must_not_contain=["Go struct field"],
        ),
    ]

    def respond(req: httpx.Request) -> tuple[int, str]:
        return (
            400,
            json.dumps({
                "error": {
                    "message": 'invalid type for field "max_tokens": expected non-negative integer, got string',
                    "type": "invalid_request",
                }
            }),
        )

    transport = _make_transport(respond)
    orig_init = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient, "__init__",
        lambda self, *a, **k: orig_init(self, *a, **{**k, "transport": transport}),
    )

    cfg = _make_cfg(tmp_path)
    results = await run_all(cfg, cases)
    assert results[0].verdict is Verdict.PASS, results[0].reasons


@pytest.mark.asyncio
async def test_runner_aggregates_and_renders(tmp_path: Path, monkeypatch):
    cases = [
        Case(id="ok", category="A", expect_status_class="2xx"),
        Case(id="bad", category="A", expect_status_class="4xx"),
    ]

    def respond(req: httpx.Request) -> tuple[int, str]:
        # Both requests get 200 — first matches expectations, second fails.
        return 200, '{"choices":[]}'

    transport = _make_transport(respond)
    orig_init = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient, "__init__",
        lambda self, *a, **k: orig_init(self, *a, **{**k, "transport": transport}),
    )

    cfg = _make_cfg(tmp_path)
    results = await run_all(cfg, cases)
    summary = aggregate(results)
    assert summary["total"] == 2
    assert summary["pass"] == 1
    assert summary["fail"] == 1
    assert summary["by_category"]["A"]["total"] == 2

    # Smoke: serializers don't crash on real Result instances.
    out = to_jsonl(results)
    assert "ok" in out and "bad" in out
    md = to_markdown(results, summary)
    assert "fy-conformance" in md
    assert "## Failures" in md


# --------------------------------------------------------------------------
# Layer 4 — backend filter / dotted paths / extra_body / expect_response_field
# --------------------------------------------------------------------------

def test_applies_to_default_runs_everywhere():
    c = _case()
    assert c.applies_to(None)
    assert c.applies_to("claude")
    assert c.applies_to("openai")


def test_applies_to_filters_when_set():
    c = _case(applies_to_backends=["openai", "deepseek"])
    assert c.applies_to(None)             # no backend specified → run
    assert c.applies_to("openai")
    assert c.applies_to("OPENAI")         # case-insensitive
    assert not c.applies_to("claude")


def test_grade_expect_response_field_present():
    c = _case(expect_status_class="2xx", expect_response_field="choices.0.message.content")
    body = '{"choices":[{"message":{"content":"hi"}}]}'
    assert grade(c, 200, body).verdict is Verdict.PASS


def test_grade_expect_response_field_missing():
    c = _case(expect_status_class="2xx", expect_response_field="choices.0.message.content")
    body = '{"choices":[]}'
    r = grade(c, 200, body)
    assert r.verdict is Verdict.FAIL
    assert any("choices.0.message.content" in reason for reason in r.reasons)


def test_grade_expect_response_field_non_json():
    c = _case(expect_status_class="2xx", expect_response_field="x.y")
    r = grade(c, 200, "<html>not json</html>")
    assert r.verdict is Verdict.FAIL


def test_build_request_body_dotted_path_override():
    baseline = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
    c = _case(override_field="messages.0.role", override_value="system")
    raw, body = build_request_body(c, baseline)
    assert raw is None
    assert body["messages"][0]["role"] == "system"
    # baseline untouched
    assert baseline["messages"][0]["role"] == "user"


def test_build_request_body_dotted_path_remove():
    baseline = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
    c = _case(remove_field="messages.0.role")
    raw, body = build_request_body(c, baseline)
    assert raw is None
    assert "role" not in body["messages"][0]
    # baseline untouched
    assert "role" in baseline["messages"][0]


def test_build_request_body_extra_body_merges():
    baseline = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
    c = _case(extra_body={"tools": [{"type": "function"}], "tool_choice": "auto"})
    _, body = build_request_body(c, baseline)
    assert body["tools"] == [{"type": "function"}]
    assert body["tool_choice"] == "auto"
    # baseline untouched
    assert "tools" not in baseline


def test_build_request_body_replace_preserves_model():
    baseline = {"model": "x", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 16}
    c = _case(body_replace={"max_tokens": 32, "messages": [{"role": "user", "content": "yo"}]})
    _, body = build_request_body(c, baseline)
    assert body["model"] == "x"          # filled in
    assert body["max_tokens"] == 32       # replaced
    assert body["messages"][0]["content"] == "yo"


@pytest.mark.asyncio
async def test_runner_skips_inapplicable_cases(tmp_path: Path, monkeypatch):
    """Cases scoped to other backends should be SKIP, not FAIL."""
    cases = [
        Case(id="any-backend", category="A", expect_status_class="2xx"),
        Case(
            id="openai-only",
            category="A",
            expect_status_class="4xx",
            applies_to_backends=["openai"],
        ),
    ]

    def respond(req: httpx.Request) -> tuple[int, str]:
        return 200, '{"ok":true}'

    transport = _make_transport(respond)
    orig_init = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient, "__init__",
        lambda self, *a, **k: orig_init(self, *a, **{**k, "transport": transport}),
    )

    cfg = _make_cfg(tmp_path)
    cfg.target.backend = "claude"
    results = await run_all(cfg, cases)

    by_id = {r.case_id: r for r in results}
    assert by_id["any-backend"].verdict is Verdict.PASS
    assert by_id["openai-only"].verdict is Verdict.SKIP
    assert by_id["openai-only"].skip_reason is not None

    summary = aggregate(results)
    assert summary["total"] == 2
    assert summary["executed"] == 1
    assert summary["pass"] == 1
    assert summary["skip"] == 1
    # pass_rate is computed against executed (excludes SKIP)
    assert summary["pass_rate"] == 1.0


def test_dataset_loads_full_corpus():
    """Make sure the shipped corpus loads without errors and has reasonable shape."""
    repo_path = Path(__file__).parent.parent / "fy_conformance/datasets/public/conformance.jsonl"
    cases = load_dataset(repo_path)
    assert len(cases) >= 100, f"corpus shrank unexpectedly: {len(cases)}"
    # all cases must have unique IDs
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case IDs in corpus"
    # leak guards must be present on every case (even smoke tests)
    for c in cases:
        if not c.must_not_contain:
            continue  # explicit empty list is allowed for unusual cases
        assert "Go struct field" in c.must_not_contain, (
            f"case {c.id!r} missing the Go struct field leak guard — "
            "every case should defend against the v1.5 regression"
        )
