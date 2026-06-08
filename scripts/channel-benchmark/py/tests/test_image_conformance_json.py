"""Tests for conformance JSON output and crash-resilience fixes."""

import json
import datetime
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field

from fy_image_conformance.report import (
    FullReport, _build_json_payload, _extract_total_cost, save_report,
)
from fy_image_conformance.config import Config, ModelProfile, Gateway, ChannelTarget, SuiteFlags, ExportConfig, BudgetConfig


def _make_config(channels=None):
    if channels is None:
        channels = [ChannelTarget(name="test-ch", pin_channel_id=42)]
    return Config(
        gateway=Gateway(
            base_url="http://localhost",
            user_token="sk-test",
            channels=channels,
        ),
        model=ModelProfile(name="gpt-image-2"),
        suites=SuiteFlags(),
        budget=BudgetConfig(),
        export=ExportConfig(output_dir="/tmp/test-out"),
    )


def _make_compat_result(channel, cases):
    @dataclass
    class FakeCase:
        name: str
        passed: bool
        elapsed_sec: float = 0.0
        detail: str = ""

    @dataclass
    class FakeCompat:
        channel: ChannelTarget
        cases: list
        passed: int = 0
        failed: int = 0

    cr = FakeCompat(channel=channel, cases=[FakeCase(**c) for c in cases])
    cr.passed = sum(1 for c in cr.cases if c.passed)
    cr.failed = len(cr.cases) - cr.passed
    return cr


def _make_safety_result(channel, cases):
    @dataclass
    class FakeCase:
        name: str
        passed: bool
        detail: str = ""

    @dataclass
    class FakeSafety:
        channel: ChannelTarget
        cases: list
        passed: int = 0

    cr = FakeSafety(channel=channel, cases=[FakeCase(**c) for c in cases])
    cr.passed = sum(1 for c in cr.cases if c.passed)
    return cr


class TestExtractTotalCost:
    def test_extracts_from_bold_format(self):
        summary = "| **合计** | | **$1.480** |"
        assert _extract_total_cost(summary) == 1.48

    def test_returns_none_for_empty(self):
        assert _extract_total_cost("") is None
        assert _extract_total_cost(None) is None

    def test_returns_none_for_no_match(self):
        assert _extract_total_cost("no cost here") is None


class TestBuildJsonPayload:
    def test_basic_structure(self):
        ch = ChannelTarget(name="supplier-A", pin_channel_id=42)
        cfg = _make_config([ch])
        report = FullReport(config=cfg)
        report.compat_results = [_make_compat_result(ch, [
            {"name": "basic_generation", "passed": True, "elapsed_sec": 30.0},
            {"name": "size_1024x1024", "passed": True, "elapsed_sec": 25.0},
            {"name": "response_format_b64", "passed": False, "elapsed_sec": 0.3},
        ])]
        report.safety_results = [_make_safety_result(ch, [
            {"name": "nsfw_rejection", "passed": True},
            {"name": "n_zero", "passed": False},
        ])]

        payloads = _build_json_payload(report)
        assert len(payloads) == 1

        p = payloads[0]
        assert p["channel_name"] == "supplier-A"
        assert p["channel_id"] == 42
        assert p["model"] == "gpt-image-2"
        assert p["api_compat"]["total"] == 3
        assert p["api_compat"]["passed"] == 2
        assert abs(p["api_compat"]["pass_rate"] - 2/3) < 0.001
        assert p["safety"]["total"] == 2
        assert p["safety"]["passed"] == 1
        assert p["safety"]["pass_rate"] == 0.5
        assert p["success_rate"] == p["api_compat"]["pass_rate"]

    def test_empty_report(self):
        cfg = _make_config()
        report = FullReport(config=cfg)
        payloads = _build_json_payload(report)
        assert len(payloads) == 1
        p = payloads[0]
        assert p["api_compat"]["total"] == 0
        assert p["safety"]["total"] == 0
        assert p["perf"]["p50_ms"] is None

    def test_multi_channel(self):
        ch1 = ChannelTarget(name="ch-A", pin_channel_id=10)
        ch2 = ChannelTarget(name="ch-B", pin_channel_id=11)
        cfg = _make_config([ch1, ch2])
        report = FullReport(config=cfg)
        report.compat_results = [
            _make_compat_result(ch1, [{"name": "basic", "passed": True}]),
            _make_compat_result(ch2, [{"name": "basic", "passed": False}]),
        ]
        payloads = _build_json_payload(report)
        assert len(payloads) == 2
        assert payloads[0]["api_compat"]["pass_rate"] == 1.0
        assert payloads[1]["api_compat"]["pass_rate"] == 0.0


class TestSaveReportJson:
    def test_saves_json_alongside_md(self, tmp_path):
        ch = ChannelTarget(name="test", pin_channel_id=1)
        cfg = _make_config([ch])
        cfg.export.output_dir = str(tmp_path)
        report = FullReport(config=cfg)
        report.compat_results = [_make_compat_result(ch, [
            {"name": "basic_generation", "passed": True, "elapsed_sec": 10.0},
        ])]

        md_path = save_report(report, str(tmp_path))
        json_path = md_path.replace(".md", ".json")

        assert md_path.endswith(".md")
        import os
        assert os.path.exists(json_path)

        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        assert "model" in data
        assert data["model"] == "gpt-image-2"
        assert "channels" in data
        assert len(data["channels"]) == 1
        assert data["channels"][0]["api_compat"]["passed"] == 1
