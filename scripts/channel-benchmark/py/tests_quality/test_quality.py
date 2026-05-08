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
