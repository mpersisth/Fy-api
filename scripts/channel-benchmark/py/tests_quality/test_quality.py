"""End-to-end tests for fy_quality using httpx.MockTransport."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

# Allow tests to import fy_quality without install when running directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fy_quality.config import Channel, Embedding, Judge, QualityConfig  # noqa: E402
from fy_quality.dataset import load_jsonl  # noqa: E402
from fy_quality.graders.deterministic import (  # noqa: E402
    ContainsGrader,
    ExactGrader,
    JsonSchemaGrader,
    RegexGrader,
)
from fy_quality.graders.pairwise import PairwiseGrader, _parse_winner  # noqa: E402
from fy_quality.graders.rubric import RubricGrader, score_from_verdict_text  # noqa: E402
from fy_quality.graders.similarity import EmbeddingClient, SimilarityGrader  # noqa: E402
from fy_quality.report import write_reports  # noqa: E402
from fy_quality.runner import QualityRunner  # noqa: E402


def _row(**kwargs):
    """Build a minimal PromptRow for grader tests."""
    from fy_quality.dataset import PromptRow
    return PromptRow(
        id=kwargs.get("id", "t"),
        kind="quality",
        prompt=kwargs.get("prompt", "hi"),
        raw={},
        category=kwargs.get("category", "test"),
        grader=kwargs.get("grader", "exact"),
        expected=kwargs.get("expected"),
        rubric=kwargs.get("rubric"),
        reference=kwargs.get("reference"),
    )


# ---------------- deterministic graders ----------------

@pytest.mark.asyncio
async def test_exact_grader_strips_quotes_and_whitespace():
    g = ExactGrader()
    r = await g.grade(_row(expected="45"), '  "45"  ')
    assert r.passed and r.score == 1.0

    r = await g.grade(_row(expected="45"), "forty-five")
    assert not r.passed


@pytest.mark.asyncio
async def test_regex_grader():
    g = RegexGrader()
    r = await g.grade(_row(expected=r"^\s*\S+\s+\S+\s+\S+\s*$"), "bright calm clear")
    assert r.passed
    r = await g.grade(_row(expected=r"^\s*\S+\s+\S+\s+\S+\s*$"), "only two words")
    # 'only two words' = 3 tokens, should match
    assert r.passed
    r = await g.grade(_row(expected=r"^\s*\S+\s+\S+\s+\S+\s*$"), "one two")
    assert not r.passed


@pytest.mark.asyncio
async def test_contains_grader_case_insensitive():
    g = ContainsGrader()
    r = await g.grade(_row(expected="Tokyo"), "The capital is TOKYO.")
    assert r.passed


@pytest.mark.asyncio
async def test_json_schema_happy_path():
    g = JsonSchemaGrader()
    schema = json.dumps({
        "type": "object",
        "required": ["name", "age"],
        "properties": {
            "name": {"type": "string", "const": "Alice"},
            "age": {"type": "number", "const": 30},
        },
        "additionalProperties": False,
    })
    r = await g.grade(_row(expected=schema), 'Here: {"name": "Alice", "age": 30}')
    assert r.passed, r.detail


@pytest.mark.asyncio
async def test_json_schema_rejects_extra_keys():
    g = JsonSchemaGrader()
    schema = json.dumps({
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    })
    r = await g.grade(_row(expected=schema), '{"name": "Alice", "age": 30}')
    assert not r.passed
    assert "unexpected keys" in r.detail


# ---------------- rubric grader ----------------

def test_rubric_score_parser():
    assert score_from_verdict_text("Looks good.\nSCORE: 5") == 5
    assert score_from_verdict_text("noisy text\n  SCORE: 3  \nmore noise") == 3
    assert score_from_verdict_text("no score here") is None
    assert score_from_verdict_text("SCORE: 99") is None  # only 1-5


class _FakeJudge:
    """Drop-in for JudgeClient that returns pre-canned verdicts."""

    def __init__(self, label: str, verdict_text: str):
        self.label = label
        self.model = "fake"
        self._text = verdict_text

    async def judge(self, prompt: str):
        from fy_quality.judge_client import JudgeVerdict
        return JudgeVerdict(raw=self._text, score=score_from_verdict_text(self._text),
                            tokens_used=42, model="fake", label=self.label)

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_rubric_dual_judge_requires_agreement():
    # Both judges >= 4 → pass
    g = RubricGrader(
        judges=[_FakeJudge("a", "ok\nSCORE: 5"), _FakeJudge("b", "ok\nSCORE: 4")],
        pass_score=4,
    )
    r = await g.grade(_row(rubric="rubric text"), "the output")
    assert r.passed
    assert r.score == pytest.approx((5 - 1) / 4 + ((4 - 1) / 4 - (5 - 1) / 4) / 2, abs=1e-6)  # avg normalised

    # One judge < 4 → fail in dual mode
    g = RubricGrader(
        judges=[_FakeJudge("a", "ok\nSCORE: 5"), _FakeJudge("b", "ok\nSCORE: 2")],
        pass_score=4,
    )
    r = await g.grade(_row(rubric="rubric text"), "the output")
    assert not r.passed
    assert "pass=4" in r.detail


@pytest.mark.asyncio
async def test_rubric_unparseable_fails_loud():
    g = RubricGrader(judges=[_FakeJudge("x", "no score tag here")], pass_score=4)
    r = await g.grade(_row(rubric="rubric text"), "output")
    assert not r.passed
    assert "unparseable" in r.detail


# ---------------- pairwise grader ----------------

def test_winner_parser():
    assert _parse_winner("because\nWINNER: A") == "A"
    assert _parse_winner("because\nwinner: tie") == "TIE"
    assert _parse_winner("no winner") is None


# ---------------- similarity grader ----------------

class _FakeEmbClient:
    """Returns deterministic pseudo-embeddings from hashing so tests are stable."""

    def __init__(self, same: bool):
        self._same = same

    async def embed(self, text: str):
        import hashlib
        h = hashlib.sha256(text.encode()).digest()
        vec = [b / 255.0 for b in h[:16]]
        if self._same:
            # force both calls to return the same vec to get cosine=1
            return [0.5] * 16, 1
        return vec, 1

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_similarity_grader_passes_on_identical():
    g = SimilarityGrader(client=_FakeEmbClient(same=True), pass_threshold=0.80)
    r = await g.grade(_row(reference="hello world"), "hello world")
    assert r.passed
    assert r.score == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_similarity_grader_fails_on_dissimilar():
    g = SimilarityGrader(client=_FakeEmbClient(same=False), pass_threshold=0.95)
    r = await g.grade(_row(reference="hello world"), "completely different text")
    # hash-based embeddings are almost certainly below 0.95 threshold
    assert not r.passed


# ---------------- dataset loader ----------------

def test_dataset_loader_filters_kind(tmp_path):
    p = tmp_path / "mixed.jsonl"
    p.write_text(
        '{"id":"a","kind":"quality","prompt":"p","grader":"exact","expected":"x"}\n'
        '{"id":"b","kind":"canary","prompt":"q","method":"alignment"}\n'
        '# comment line\n'
        '\n'
    )
    rows = load_jsonl(p, kind="quality")
    assert [r.id for r in rows] == ["a"]
    rows = load_jsonl(p, kind="canary")
    assert [r.id for r in rows] == ["b"]


def test_dataset_loader_rejects_dupes(tmp_path):
    p = tmp_path / "dup.jsonl"
    p.write_text(
        '{"id":"a","kind":"quality","prompt":"p","grader":"exact","expected":"x"}\n'
        '{"id":"a","kind":"quality","prompt":"p2","grader":"exact","expected":"y"}\n'
    )
    with pytest.raises(ValueError, match="duplicate ids"):
        load_jsonl(p)


# ---------------- full runner (mocked upstream) ----------------

def _make_mock_channel_transport(output_text: str = "pong"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": output_text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_full_runner_with_mock_upstream(tmp_path, monkeypatch):
    dataset = tmp_path / "q.jsonl"
    dataset.write_text(
        '{"id":"t1","kind":"quality","category":"t","grader":"exact","prompt":"?","expected":"pong"}\n'
        '{"id":"t2","kind":"quality","category":"t","grader":"contains","prompt":"?","expected":"ng"}\n'
    )

    cfg = QualityConfig(
        channels=[Channel(name="test-ch", model="m", token="sk-t", base_url="http://mock")],
        dataset=str(dataset),
        judges=[],
        embedding=None,
        cache_dir=str(tmp_path / "cache"),
        output_dir=str(tmp_path / "out"),
        concurrency=2,
    )

    # Patch the runner's private _generate to route through the mock transport.
    # Simpler: monkey-patch httpx.AsyncClient globally to use mock transport.
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = _make_mock_channel_transport("pong")
        return orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    runner = QualityRunner(cfg)
    report = await runner.run()

    assert len(report.per_prompt) == 2
    assert all(p.passed for p in report.per_prompt), [
        (p.prompt_id, p.detail) for p in report.per_prompt
    ]

    files = write_reports(report, ["json", "csv", "markdown"], cfg.output_dir)
    assert len(files) == 3
    for f in files:
        assert f.exists() and f.stat().st_size > 0


# ---------------- perturbation / contamination defense ----------------

def test_perturbation_deterministic():
    from fy_quality.perturbation import apply_perturbations

    a = apply_perturbations(
        "Compute the sum of 3 and 4.",
        seed=123, prompt_id="x", strategies=["whitespace", "trailing_marker"],
    )
    b = apply_perturbations(
        "Compute the sum of 3 and 4.",
        seed=123, prompt_id="x", strategies=["whitespace", "trailing_marker"],
    )
    assert a == b
    c = apply_perturbations(
        "Compute the sum of 3 and 4.",
        seed=124, prompt_id="x", strategies=["whitespace", "trailing_marker"],
    )
    assert c != a  # different seed → different perturbation


def test_perturbation_whitespace_inserts_zwsp():
    from fy_quality.perturbation import ZERO_WIDTH_SPACE, apply_perturbations

    out = apply_perturbations(
        "hello world",
        seed=1, prompt_id="p", strategies=["whitespace"],
    )
    assert ZERO_WIDTH_SPACE in out
    # Original characters are preserved; only ZWSP is added.
    assert out.replace(ZERO_WIDTH_SPACE, "") == "hello world"


def test_perturbation_trailing_marker_is_html_comment():
    from fy_quality.perturbation import apply_perturbations

    out = apply_perturbations(
        "What is 1+1?",
        seed=5, prompt_id="p", strategies=["trailing_marker"],
    )
    assert out.startswith("What is 1+1?")
    assert "<!--fq" in out and out.rstrip().endswith("-->")


def test_perturbation_synonym_preserves_case_and_punct():
    from fy_quality.perturbation import apply_perturbations

    out = apply_perturbations(
        "Compute, then respond.",
        seed=0, prompt_id="p", strategies=["synonym"],
    )
    # First hit is "Compute," → "Calculate," (cap preserved, comma kept).
    assert out.startswith("Calculate,")


def test_perturbation_unknown_strategy_raises():
    import pytest
    from fy_quality.perturbation import apply_perturbations

    with pytest.raises(ValueError, match="unknown perturbation"):
        apply_perturbations("x", seed=1, prompt_id="p", strategies=["nope"])


def test_wire_prompt_noop_without_perturbations():
    row = _row(prompt="Plain prompt.")
    assert row.wire_prompt() == "Plain prompt."


def test_wire_prompt_applies_perturbations():
    from fy_quality.dataset import PromptRow
    from fy_quality.perturbation import ZERO_WIDTH_SPACE

    row = PromptRow(
        id="t", kind="quality", prompt="What is 2+2?", raw={},
        grader="exact", seed=7,
        perturbations=["whitespace", "trailing_marker"],
    )
    wire = row.wire_prompt()
    assert wire != "What is 2+2?"
    assert ZERO_WIDTH_SPACE in wire
    assert "<!--fq" in wire


@pytest.mark.asyncio
async def test_runner_sends_perturbed_prompt(tmp_path, monkeypatch):
    """The channel must see the PERTURBED prompt, not the raw file text."""
    dataset = tmp_path / "q.jsonl"
    dataset.write_text(
        '{"id":"t1","kind":"quality","category":"t","grader":"exact",'
        '"prompt":"What is 2+2?","expected":"4",'
        '"seed":42,"perturbations":["whitespace","trailing_marker"]}\n'
    )

    cfg = QualityConfig(
        channels=[Channel(name="test-ch", model="m", token="sk-t", base_url="http://mock")],
        dataset=str(dataset),
        judges=[],
        embedding=None,
        cache_dir=str(tmp_path / "cache"),
        output_dir=str(tmp_path / "out"),
        concurrency=1,
    )

    received_prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        received_prompts.append(body["messages"][-1]["content"])
        return httpx.Response(
            200, json={
                "choices": [{"message": {"content": "4"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            },
        )

    orig_init = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient, "__init__",
        lambda self, *a, **k: orig_init(
            self, *a, **{**k, "transport": httpx.MockTransport(handler)}
        ),
    )

    report = await QualityRunner(cfg).run()
    assert len(report.per_prompt) == 1
    assert report.per_prompt[0].passed

    # The prompt on the wire must differ from the file text.
    assert len(received_prompts) == 1
    from fy_quality.perturbation import ZERO_WIDTH_SPACE
    assert received_prompts[0] != "What is 2+2?"
    assert ZERO_WIDTH_SPACE in received_prompts[0]


def _make_mock_channel_transport_with_capture(capture: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        capture.append(body["messages"][-1]["content"])
        return httpx.Response(
            200, json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )
    return httpx.MockTransport(handler)


# ---------------- channel pinning (Stage 2) ----------------

@pytest.mark.asyncio
async def test_runner_pins_channel_via_token_suffix(tmp_path, monkeypatch):
    """When Channel.pin_channel_id is set, the Authorization header sent to
    /v1/chat/completions becomes `Bearer <token>-<id>` — the admin-only syntax
    Fy-api parses in middleware/auth.go (~line 431) to force a specific
    channel. Without the field, the suffix MUST NOT appear."""

    dataset = tmp_path / "q.jsonl"
    dataset.write_text(
        '{"id":"t1","kind":"quality","category":"t","grader":"exact",'
        '"prompt":"?","expected":"ok"}\n'
    )

    received_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received_auth.append(request.headers.get("Authorization", ""))
        return httpx.Response(
            200, json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    orig_init = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient, "__init__",
        lambda self, *a, **k: orig_init(
            self, *a, **{**k, "transport": httpx.MockTransport(handler)}
        ),
    )

    # Case 1: pinned channel.
    received_auth.clear()
    cfg = QualityConfig(
        channels=[Channel(
            name="ch-pin", model="m", token="sk-admin",
            base_url="http://mock", pin_channel_id=42,
        )],
        dataset=str(dataset),
        judges=[], embedding=None,
        cache_dir=str(tmp_path / "cache1"),
        output_dir=str(tmp_path / "out1"),
        concurrency=1,
    )
    await QualityRunner(cfg).run()
    assert received_auth == ["Bearer sk-admin-42"], received_auth

    # Case 2: no pin → no suffix.
    received_auth.clear()
    cfg2 = QualityConfig(
        channels=[Channel(
            name="ch-nopin", model="m", token="sk-admin",
            base_url="http://mock", pin_channel_id=None,
        )],
        dataset=str(dataset),
        judges=[], embedding=None,
        cache_dir=str(tmp_path / "cache2"),
        output_dir=str(tmp_path / "out2"),
        concurrency=1,
    )
    await QualityRunner(cfg2).run()
    assert received_auth == ["Bearer sk-admin"], received_auth


def test_quality_config_parses_pin_channel_id(tmp_path):
    """YAML round-trip: pin_channel_id is read back as int when present, None
    otherwise. Back-compat: configs without the field still parse."""
    p = tmp_path / "q.yaml"
    p.write_text(
        "channels:\n"
        "  - name: a\n"
        "    model: m\n"
        "    token: sk-a\n"
        "    base_url: http://mock\n"
        "    pin_channel_id: 8\n"
        "  - name: b\n"
        "    model: m\n"
        "    token: sk-b\n"
        "    base_url: http://mock\n"
        "dataset: /dev/null\n"
    )
    cfg = QualityConfig.load(p)
    assert cfg.channels[0].pin_channel_id == 8
    assert cfg.channels[1].pin_channel_id is None


def test_quality_config_rejects_nonpositive_pin(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "channels:\n"
        "  - name: a\n"
        "    model: m\n"
        "    token: sk-a\n"
        "    base_url: http://mock\n"
        "    pin_channel_id: 0\n"
        "dataset: /dev/null\n"
    )
    import pytest
    with pytest.raises(ValueError, match="must be > 0"):
        QualityConfig.load(p)
