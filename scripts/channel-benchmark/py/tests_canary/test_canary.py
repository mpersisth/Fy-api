"""End-to-end tests for fy_canary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fy_canary.alignment import (  # noqa: E402
    _levenshtein,
    evaluate_alignment,
    normalized_edit_similarity,
)
from fy_canary.baseline import BaselineStore, ChannelBaseline, ProbeBaseline  # noqa: E402
from fy_canary.config import CanaryConfig, CanarySource  # noqa: E402
from fy_canary.drift import centroid, cosine, evaluate_drift  # noqa: E402
from fy_canary.runner import CanaryRunner  # noqa: E402


# --------- alignment -------------------------------------------------

def test_levenshtein_trivial():
    assert _levenshtein("", "") == 0
    assert _levenshtein("abc", "") == 3
    assert _levenshtein("", "abc") == 3
    assert _levenshtein("kitten", "sitting") == 3


def test_normalized_edit_similarity_bounds():
    assert normalized_edit_similarity("", "") == 1.0
    assert normalized_edit_similarity("a", "") == 0.0
    assert normalized_edit_similarity("same", "same") == 1.0
    sim = normalized_edit_similarity("hello world", "hello WORLD")
    assert 0.0 < sim < 1.0


def test_evaluate_alignment_passes_identical():
    v = evaluate_alignment(
        prompt_id="x",
        baseline_sample="This is a test response.",
        current_sample="This is a test response.",
        threshold=0.70,
    )
    assert v.passed and v.similarity == 1.0


def test_evaluate_alignment_fails_on_substitution():
    v = evaluate_alignment(
        prompt_id="x",
        baseline_sample="I'm sorry, but I can't assist with that.",
        current_sample="I cannot help you with this request.",
        threshold=0.80,
    )
    assert not v.passed


# --------- drift -------------------------------------------------

def test_centroid_and_cosine():
    vs = [[1.0, 0.0], [0.0, 1.0]]
    c = centroid(vs)
    assert c == [0.5, 0.5]
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_evaluate_drift_passes_close_centroids():
    v = evaluate_drift(
        prompt_id="x",
        baseline_centroid=[1.0, 0.0, 0.0],
        current_centroid=[0.99, 0.01, 0.0],
        n_samples=8,
        threshold=0.93,
    )
    assert v.passed


def test_evaluate_drift_fails_on_shift():
    v = evaluate_drift(
        prompt_id="x",
        baseline_centroid=[1.0, 0.0, 0.0],
        current_centroid=[0.0, 1.0, 0.0],
        n_samples=8,
        threshold=0.93,
    )
    assert not v.passed


# --------- baseline store -------------------------------------------------

def test_baseline_roundtrip(tmp_path):
    store = BaselineStore(tmp_path)
    baseline = ChannelBaseline(
        source_name="test-ch",
        model="m",
        created_at_unix=1_700_000_000.0,
        probes={
            "a": ProbeBaseline(prompt_id="a", method="alignment", samples=["hello"]),
            "b": ProbeBaseline(prompt_id="b", method="drift", samples=["x", "y"], centroid=[0.1, 0.2]),
        },
    )
    path = store.save(baseline)
    assert path.exists()

    loaded = store.load("test-ch")
    assert loaded is not None
    assert loaded.source_name == "test-ch"
    assert set(loaded.probes) == {"a", "b"}
    assert loaded.probes["b"].centroid == [0.1, 0.2]


def test_baseline_missing_returns_none(tmp_path):
    store = BaselineStore(tmp_path)
    assert store.load("nope") is None


# --------- full runner (baseline + audit) -------------------------------------------------

def _mock_transport(completion_text: str = "baseline-text"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": completion_text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
        )
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_baseline_then_audit_all_pass(tmp_path, monkeypatch):
    dataset = tmp_path / "c.jsonl"
    dataset.write_text(
        '{"id":"a1","kind":"canary","method":"alignment","prompt":"?","max_tokens":50,"temperature":0.0}\n'
    )

    cfg = CanaryConfig(
        source=CanarySource(
            name="mock-source", base_url="http://mock",
            api_key="sk", model="m",
        ),
        dataset=str(dataset),
        baselines_dir=str(tmp_path / "baselines"),
        output_dir=str(tmp_path / "out"),
        embedding=None, mmd_enabled=False,
        mmd_n_samples=1, concurrency=2,
    )

    orig = httpx.AsyncClient.__init__
    monkeypatch.setattr(httpx.AsyncClient, "__init__", lambda self, *a, **k: orig(
        self, *a, **{**k, "transport": _mock_transport("stable-response")}
    ))

    runner = CanaryRunner(cfg)
    baseline = await runner.build_baseline()
    assert "a1" in baseline.probes
    assert baseline.probes["a1"].samples == ["stable-response"]

    # Audit with the SAME mock returning same text → should pass.
    report = await runner.audit()
    assert len(report.outcomes) == 1
    assert report.outcomes[0].passed, report.outcomes[0].detail


@pytest.mark.asyncio
async def test_audit_flags_substitution(tmp_path, monkeypatch):
    dataset = tmp_path / "c.jsonl"
    dataset.write_text(
        '{"id":"a1","kind":"canary","method":"alignment","prompt":"?","max_tokens":50,"temperature":0.0}\n'
    )
    cfg = CanaryConfig(
        source=CanarySource(
            name="mock-src", base_url="http://mock", api_key="sk", model="m",
        ),
        dataset=str(dataset),
        baselines_dir=str(tmp_path / "baselines"),
        output_dir=str(tmp_path / "out"),
        embedding=None, mmd_enabled=False, concurrency=1, mmd_n_samples=1,
    )

    # Baseline: channel returns "original response exactly".
    orig = httpx.AsyncClient.__init__

    def install_transport(text: str):
        monkeypatch.setattr(
            httpx.AsyncClient, "__init__",
            lambda self, *a, **k: orig(self, *a, **{**k, "transport": _mock_transport(text)}),
        )

    install_transport("I'm sorry, but I'm not able to help with that request.")
    await CanaryRunner(cfg).build_baseline()

    # Audit: channel now returns a totally different refusal phrasing.
    install_transport("I won't assist you here.")
    report = await CanaryRunner(cfg).audit()

    assert len(report.outcomes) == 1
    assert not report.outcomes[0].passed
    assert "edit-sim" in report.outcomes[0].detail


@pytest.mark.asyncio
async def test_audit_without_baseline_errors(tmp_path):
    dataset = tmp_path / "c.jsonl"
    dataset.write_text(
        '{"id":"a1","kind":"canary","method":"alignment","prompt":"?","max_tokens":50,"temperature":0.0}\n'
    )
    cfg = CanaryConfig(
        source=CanarySource(name="nope", base_url="http://mock", api_key="sk", model="m"),
        dataset=str(dataset),
        baselines_dir=str(tmp_path / "baselines"),
        output_dir=str(tmp_path / "out"),
        embedding=None, mmd_enabled=False,
    )
    with pytest.raises(FileNotFoundError):
        await CanaryRunner(cfg).audit()


# --------- baseline health + v1 backwards compat --------------------------

def test_baseline_save_writes_v2_metadata(tmp_path):
    store = BaselineStore(tmp_path)
    baseline = ChannelBaseline(
        source_name="hc-src", model="m",
        created_at_unix=1_700_000_000.0,
        probes={
            "a": ProbeBaseline(prompt_id="a", method="alignment", samples=["hi"]),
            "b": ProbeBaseline(prompt_id="b", method="drift",
                               samples=["x", "y", "z"], centroid=[0.1, 0.2]),
        },
    )
    path = store.save(baseline)
    raw = json.loads(path.read_text())
    assert raw["schema_version"] == 2
    assert raw["n_probes"] == 2
    assert raw["total_samples"] == 4
    assert "recorded_at_iso" in raw
    assert raw["fy_canary_version"]
    assert raw["recorded_at_iso"].startswith("2023-")


def test_baseline_loads_v1_file_without_metadata(tmp_path):
    # Write a legacy v1 file by hand and ensure load() succeeds.
    p = tmp_path / "legacy.json"
    p.write_text(json.dumps({
        "source_name": "legacy",
        "model": "m",
        "created_at_unix": 1_700_000_000.0,
        "probes": {
            "a": {"prompt_id": "a", "method": "alignment", "samples": ["hi"], "centroid": None},
        },
    }))
    store = BaselineStore(tmp_path)
    b = store.load("legacy")
    assert b is not None
    assert b.schema_version == 1                     # read from raw
    assert b.recorded_at_iso.startswith("2023-")     # auto-derived from unix ts
    assert b.n_probes == 1

    # Re-saving should upgrade it to v2.
    store.save(b)
    raw = json.loads(p.read_text())
    assert raw["schema_version"] == 2


def test_runner_baseline_health_none_when_missing(tmp_path):
    dataset = tmp_path / "c.jsonl"
    dataset.write_text(
        '{"id":"a1","kind":"canary","method":"alignment","prompt":"?"}\n'
    )
    cfg = CanaryConfig(
        source=CanarySource(name="nope", base_url="http://mock", api_key="sk", model="m"),
        dataset=str(dataset),
        baselines_dir=str(tmp_path / "baselines"),
        output_dir=str(tmp_path / "out"),
        embedding=None,
    )
    assert CanaryRunner(cfg).baseline_health() is None


def test_runner_baseline_health_flags_stale(tmp_path):
    import time as _time

    dataset = tmp_path / "c.jsonl"
    dataset.write_text(
        '{"id":"a1","kind":"canary","method":"alignment","prompt":"?"}\n'
    )

    cfg = CanaryConfig(
        source=CanarySource(name="old", base_url="http://mock", api_key="sk", model="m"),
        dataset=str(dataset),
        baselines_dir=str(tmp_path / "baselines"),
        output_dir=str(tmp_path / "out"),
        embedding=None,
        baseline_max_age_days=7,
    )
    runner = CanaryRunner(cfg)
    # Force a baseline timestamp 30 days in the past.
    runner.store.save(ChannelBaseline(
        source_name="old", model="m",
        created_at_unix=_time.time() - 30 * 86400,
        probes={"a": ProbeBaseline(prompt_id="a", method="alignment", samples=["hi"])},
    ))
    health = runner.baseline_health()
    assert health is not None
    assert health["stale"] is True
    assert health["age_days"] >= 29
    assert health["max_age_days"] == 7
    assert health["n_probes"] == 1
    assert health["total_samples"] == 1


def test_runner_baseline_health_passes_fresh(tmp_path):
    dataset = tmp_path / "c.jsonl"
    dataset.write_text('{"id":"a1","kind":"canary","method":"alignment","prompt":"?"}\n')
    cfg = CanaryConfig(
        source=CanarySource(name="fresh", base_url="http://mock", api_key="sk", model="m"),
        dataset=str(dataset),
        baselines_dir=str(tmp_path / "baselines"),
        output_dir=str(tmp_path / "out"),
        embedding=None,
        baseline_max_age_days=30,
    )
    runner = CanaryRunner(cfg)
    runner.store.save(ChannelBaseline(
        source_name="fresh", model="m",
        created_at_unix=__import__("time").time(),
        probes={"a": ProbeBaseline(prompt_id="a", method="alignment", samples=["hi"])},
    ))
    health = runner.baseline_health()
    assert health["stale"] is False


# --------- verify-baseline -------------------------------------------------

@pytest.mark.asyncio
async def test_verify_baseline_passes_when_source_stable(tmp_path, monkeypatch):
    dataset = tmp_path / "c.jsonl"
    dataset.write_text(
        '{"id":"a1","kind":"canary","method":"alignment","prompt":"?",'
        '"max_tokens":50,"temperature":0.0}\n'
    )
    cfg = CanaryConfig(
        source=CanarySource(
            name="verify-stable", base_url="http://mock", api_key="sk", model="m",
        ),
        dataset=str(dataset),
        baselines_dir=str(tmp_path / "baselines"),
        output_dir=str(tmp_path / "out"),
        embedding=None,
        mmd_enabled=False, concurrency=1, mmd_n_samples=1,
    )

    orig = httpx.AsyncClient.__init__
    monkeypatch.setattr(httpx.AsyncClient, "__init__", lambda self, *a, **k: orig(
        self, *a, **{**k, "transport": _mock_transport("steady-response")}
    ))

    runner = CanaryRunner(cfg)
    await runner.build_baseline()
    report = await runner.verify_baseline()
    assert report.mode == "verify-baseline"
    assert len(report.outcomes) == 1
    assert report.outcomes[0].passed


@pytest.mark.asyncio
async def test_verify_baseline_flags_source_drift(tmp_path, monkeypatch):
    """If the SOURCE itself has drifted, verify-baseline must notice."""
    dataset = tmp_path / "c.jsonl"
    dataset.write_text(
        '{"id":"a1","kind":"canary","method":"alignment","prompt":"?",'
        '"max_tokens":50,"temperature":0.0}\n'
    )
    cfg = CanaryConfig(
        source=CanarySource(
            name="verify-drift", base_url="http://mock", api_key="sk", model="m",
        ),
        dataset=str(dataset),
        baselines_dir=str(tmp_path / "baselines"),
        output_dir=str(tmp_path / "out"),
        embedding=None,
        mmd_enabled=False, concurrency=1, mmd_n_samples=1,
    )

    orig = httpx.AsyncClient.__init__

    def install(text: str):
        monkeypatch.setattr(
            httpx.AsyncClient, "__init__",
            lambda self, *a, **k: orig(self, *a, **{**k, "transport": _mock_transport(text)}),
        )

    install("The original canonical refusal phrasing from the vendor.")
    await CanaryRunner(cfg).build_baseline()

    # Source has changed its phrasing — verify should flag that.
    install("Completely different sentence structure now, longer too.")
    report = await CanaryRunner(cfg).verify_baseline()
    assert len(report.outcomes) == 1
    assert not report.outcomes[0].passed


@pytest.mark.asyncio
async def test_verify_baseline_without_baseline_errors(tmp_path):
    dataset = tmp_path / "c.jsonl"
    dataset.write_text('{"id":"a1","kind":"canary","method":"alignment","prompt":"?"}\n')
    cfg = CanaryConfig(
        source=CanarySource(name="nobaseline", base_url="http://mock", api_key="sk", model="m"),
        dataset=str(dataset),
        baselines_dir=str(tmp_path / "baselines"),
        output_dir=str(tmp_path / "out"),
        embedding=None,
    )
    with pytest.raises(FileNotFoundError):
        await CanaryRunner(cfg).verify_baseline()
