"""Tests for fy_integrity module.

Layer 1: Unit tests (no I/O)
Layer 2: Config loading (file I/O)
Layer 3: Async integration with MockTransport
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from fy_integrity.config import IntegrityConfig
from fy_integrity.probes.cache import CacheIntegrityProbe
from fy_integrity.probes.determinism import DeterminismProbe, _pairwise_consistency
from fy_integrity.probes.inflation import TokenInflationProbe, _count_message_tokens
from fy_integrity.probes.stream import (
    StreamRepackagingProbe,
    _burst_ratio,
    _coefficient_of_variation,
)
from fy_integrity.probes.tool_use import ToolUsePassthroughProbe


# ---------------------------------------------------------------------------
# Layer 1: Pure unit tests
# ---------------------------------------------------------------------------


class TestPairwiseConsistency:
    def test_all_identical(self):
        assert _pairwise_consistency(["a", "a", "a"]) == 1.0

    def test_all_different(self):
        assert _pairwise_consistency(["a", "b", "c"]) == 0.0

    def test_partial(self):
        # 3 pairs: (a,a)=match, (a,b)=no, (a,b)=no => 1/3
        assert abs(_pairwise_consistency(["a", "a", "b"]) - 1 / 3) < 0.01

    def test_single(self):
        assert _pairwise_consistency(["a"]) == 1.0


class TestBurstRatio:
    def test_no_bursts(self):
        gaps = [0.05, 0.06, 0.04, 0.05]  # all > 10ms
        assert _burst_ratio(gaps) == 0.0

    def test_all_bursts(self):
        gaps = [0.001, 0.002, 0.005, 0.009]  # all < 10ms
        assert _burst_ratio(gaps) == 1.0

    def test_mixed(self):
        gaps = [0.001, 0.05, 0.002, 0.06]  # 2/4 < 10ms
        assert _burst_ratio(gaps) == 0.5

    def test_empty(self):
        assert _burst_ratio([]) == 0.0


class TestCoefficientOfVariation:
    def test_uniform(self):
        assert _coefficient_of_variation([0.05, 0.05, 0.05]) == 0.0

    def test_varied(self):
        cv = _coefficient_of_variation([0.01, 0.1, 0.01, 0.1])
        assert cv > 0.5


class TestTokenCount:
    def test_basic_message(self):
        try:
            import tiktoken
        except ImportError:
            pytest.skip("tiktoken not installed")
        enc = tiktoken.get_encoding("cl100k_base")
        msgs = [{"role": "user", "content": "hello"}]
        count = _count_message_tokens(enc, msgs)
        assert 5 <= count <= 10


# ---------------------------------------------------------------------------
# Layer 2: Config loading tests
# ---------------------------------------------------------------------------


class TestConfigLoading:
    def test_loads_full_yaml(self, tmp_path):
        cfg_file = tmp_path / "test.yaml"
        cfg_file.write_text(
            """
gateway:
  base_url: "http://localhost:3000"
  user_token: "sk-test"
  pin_channel_id: 30

target:
  model: "claude-opus-4-7"
  max_tokens: 128

probes:
  cache: { enabled: true, rounds: 3 }
  inflation: { enabled: false }
  determinism: { enabled: true, rounds: 3, min_consistency: 0.9 }
  tool_use: { enabled: true }
  stream: { enabled: true, rounds: 2, burst_threshold: 0.6 }
  filtering: { enabled: false }
  isolation: { enabled: false }

export:
  formats: [json, markdown]
  output_dir: "test-results"
"""
        )
        cfg = IntegrityConfig.load(cfg_file)
        assert cfg.target.model == "claude-opus-4-7"
        assert cfg.gateway.pin_channel_id == 30
        assert cfg.probes.cache.rounds == 3
        assert cfg.probes.inflation.enabled is False
        assert cfg.probes.stream.burst_threshold == 0.6

    def test_env_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_URL", "http://example.com")
        monkeypatch.setenv("TEST_TOKEN", "sk-abc")
        cfg_file = tmp_path / "env.yaml"
        cfg_file.write_text(
            """
gateway:
  base_url: "${TEST_URL}"
  user_token: "${TEST_TOKEN}"
target:
  model: "test-model"
"""
        )
        cfg = IntegrityConfig.load(cfg_file)
        assert cfg.gateway.base_url == "http://example.com"
        assert cfg.gateway.user_token == "sk-abc"

    def test_missing_env_raises(self, tmp_path):
        cfg_file = tmp_path / "bad.yaml"
        cfg_file.write_text(
            """
gateway:
  base_url: "${NONEXISTENT_VAR_XYZ}"
  user_token: "sk-test"
target:
  model: "test"
"""
        )
        with pytest.raises(ValueError, match="undefined environment"):
            IntegrityConfig.load(cfg_file)

    def test_isolation_requires_secondary_token(self, tmp_path):
        cfg_file = tmp_path / "iso.yaml"
        cfg_file.write_text(
            """
gateway:
  base_url: "http://localhost"
  user_token: "sk-test"
target:
  model: "test"
probes:
  isolation: { enabled: true, rounds: 3 }
"""
        )
        with pytest.raises(ValueError, match="secondary_token"):
            IntegrityConfig.load(cfg_file)


# ---------------------------------------------------------------------------
# Layer 3: Async integration tests with MockTransport
# ---------------------------------------------------------------------------


def _make_completion_response(
    content: str = "4",
    prompt_tokens: int = 10,
    completion_tokens: int = 1,
    cached_tokens: int = 0,
    tool_calls: list | None = None,
) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "choices": [{"message": msg, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": cached_tokens},
        },
    }


def _mock_transport(response_fn):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_fn(request))

    return httpx.MockTransport(handler)


class TestCacheProbeIntegration:
    async def test_passes_when_no_cache(self, monkeypatch):
        transport = _mock_transport(
            lambda r: _make_completion_response(cached_tokens=0)
        )
        from fy_integrity.client import IntegrityClient
        from fy_integrity.config import IntegrityConfig

        cfg = _minimal_config(cache_rounds=3)
        async with IntegrityClient(
            base_url="http://test", token="sk-t", timeout_sec=5
        ) as client:
            client._client = httpx.AsyncClient(transport=transport)
            probe = CacheIntegrityProbe()
            result = await probe.run(client, cfg)
        assert result.passed

    async def test_fails_when_cache_detected(self, monkeypatch):
        transport = _mock_transport(
            lambda r: _make_completion_response(cached_tokens=173)
        )
        from fy_integrity.client import IntegrityClient

        cfg = _minimal_config(cache_rounds=3)
        async with IntegrityClient(
            base_url="http://test", token="sk-t", timeout_sec=5
        ) as client:
            client._client = httpx.AsyncClient(transport=transport)
            probe = CacheIntegrityProbe()
            result = await probe.run(client, cfg)
        assert not result.passed
        assert result.severity == "critical"
        assert "cache_read > 0" in result.summary


class TestToolUseProbeIntegration:
    async def test_passes_correct_prefix(self):
        transport = _mock_transport(
            lambda r: _make_completion_response(
                content="",
                tool_calls=[
                    {
                        "id": "toolu_abc123",
                        "type": "function",
                        "function": {"name": "get_current_weather", "arguments": '{"location":"Tokyo"}'},
                    }
                ],
            )
        )
        from fy_integrity.client import IntegrityClient

        cfg = _minimal_config()
        async with IntegrityClient(
            base_url="http://test", token="sk-t", timeout_sec=5
        ) as client:
            client._client = httpx.AsyncClient(transport=transport)
            probe = ToolUsePassthroughProbe()
            result = await probe.run(client, cfg)
        assert result.passed

    async def test_fails_rewritten_prefix(self):
        transport = _mock_transport(
            lambda r: _make_completion_response(
                content="",
                tool_calls=[
                    {
                        "id": "tooluse_xyz789",
                        "type": "function",
                        "function": {"name": "get_current_weather", "arguments": '{"location":"Tokyo"}'},
                    }
                ],
            )
        )
        from fy_integrity.client import IntegrityClient

        cfg = _minimal_config()
        async with IntegrityClient(
            base_url="http://test", token="sk-t", timeout_sec=5
        ) as client:
            client._client = httpx.AsyncClient(transport=transport)
            probe = ToolUsePassthroughProbe()
            result = await probe.run(client, cfg)
        assert not result.passed
        assert result.severity == "critical"


class TestDeterminismProbeIntegration:
    async def test_passes_consistent_responses(self):
        transport = _mock_transport(
            lambda r: _make_completion_response(content="4")
        )
        from fy_integrity.client import IntegrityClient

        cfg = _minimal_config(determinism_rounds=5)
        async with IntegrityClient(
            base_url="http://test", token="sk-t", timeout_sec=5
        ) as client:
            client._client = httpx.AsyncClient(transport=transport)
            probe = DeterminismProbe()
            result = await probe.run(client, cfg)
        assert result.passed

    async def test_fails_inconsistent_responses(self):
        counter = {"n": 0}

        def varying_response(r):
            counter["n"] += 1
            content = "4" if counter["n"] % 2 == 0 else "four"
            return _make_completion_response(content=content)

        transport = _mock_transport(varying_response)
        from fy_integrity.client import IntegrityClient

        cfg = _minimal_config(determinism_rounds=5, min_consistency=0.95)
        async with IntegrityClient(
            base_url="http://test", token="sk-t", timeout_sec=5
        ) as client:
            client._client = httpx.AsyncClient(transport=transport)
            probe = DeterminismProbe()
            result = await probe.run(client, cfg)
        assert not result.passed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_config(
    cache_rounds=5,
    determinism_rounds=5,
    min_consistency=0.95,
):
    from fy_integrity.config import (
        CacheProbeConfig,
        DeterminismProbeConfig,
        ExportCfg,
        FilteringProbeConfig,
        GatewayCfg,
        InflationProbeConfig,
        IntegrityConfig,
        IsolationProbeConfig,
        ProbesCfg,
        StreamProbeConfig,
        TargetCfg,
        ToolUseProbeConfig,
    )

    return IntegrityConfig(
        gateway=GatewayCfg(base_url="http://test", user_token="sk-test"),
        target=TargetCfg(model="test-model", max_tokens=64),
        probes=ProbesCfg(
            cache=CacheProbeConfig(enabled=True, rounds=cache_rounds),
            inflation=InflationProbeConfig(enabled=True, tolerance_tokens=10),
            determinism=DeterminismProbeConfig(
                enabled=True, rounds=determinism_rounds, min_consistency=min_consistency
            ),
            tool_use=ToolUseProbeConfig(enabled=True),
            stream=StreamProbeConfig(enabled=True, rounds=3, burst_threshold=0.5),
            filtering=FilteringProbeConfig(enabled=True),
            isolation=IsolationProbeConfig(enabled=False),
        ),
        export=ExportCfg(),
    )
