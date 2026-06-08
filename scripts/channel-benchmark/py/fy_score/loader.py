"""Load result JSONs from each benchmark tool and extract scoring inputs."""

from __future__ import annotations

import json
import re
import warnings
from dataclasses import dataclass
from pathlib import Path


def _read_json(path: Path) -> dict:
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except (UnicodeDecodeError, ValueError):
            continue
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _extract_channel_id(name: str) -> int | None:
    """Extract channel id from names like 'claude-opus-4-7-ch3' or '长安数科(id=3)'."""
    m = re.search(r"-ch(\d+)$", name)
    if m:
        return int(m.group(1))
    m = re.search(r"\(id=(\d+)\)", name)
    if m:
        return int(m.group(1))
    return None


@dataclass
class SmokeMetrics:
    channel_name: str
    channel_id: int | None
    model: str
    success_rate: float


@dataclass
class LoadtestMetrics:
    channel_name: str
    channel_id: int | None
    model: str
    ttft_p95_ms: float | None
    e2e_p95_ms: float | None
    throughput_toks: float | None


@dataclass
class QualityMetrics:
    channel_name: str
    channel_id: int | None
    model: str
    pass_rate: float
    avg_score: float


@dataclass
class CanaryMetrics:
    channel_name: str
    channel_id: int | None
    model: str
    probe_pass_rate: float
    avg_probe_score: float


@dataclass
class ConformanceMetrics:
    channel_name: str
    channel_id: int | None
    model: str
    pass_rate: float


@dataclass
class IntegrityMetrics:
    channel_name: str
    channel_id: int | None
    model: str
    probes: list[dict]


def load_smoke(path: Path) -> list[SmokeMetrics]:
    """Parse Go smoke-test JSON."""
    data = _read_json(path)
    results: list[SmokeMetrics] = []
    for item in data.get("results", []):
        rate = item.get("SuccessRatePct", 0.0) / 100.0
        results.append(SmokeMetrics(
            channel_name=item.get("ChannelName", ""),
            channel_id=item.get("ChannelID"),
            model=item.get("Model", ""),
            success_rate=rate,
        ))
    return results


def load_loadtest(path: Path) -> list[LoadtestMetrics]:
    """Parse fy-loadtest JSON. Uses concurrency=1 level (baseline)."""
    data = _read_json(path)
    model = data.get("model", "")
    results: list[LoadtestMetrics] = []
    for ch in data.get("channels", []):
        levels = ch.get("levels", [])
        baseline = next((lv for lv in levels if lv.get("concurrency") == 1), levels[0] if levels else None)
        if baseline is None:
            continue
        ttft = baseline.get("ttft", {})
        e2e = baseline.get("e2e", {})
        toks = baseline.get("aggregate_tok_per_s")
        results.append(LoadtestMetrics(
            channel_name=ch.get("channel_name", ""),
            channel_id=ch.get("pin_channel_id"),
            model=model,
            ttft_p95_ms=ttft.get("p95_ms"),
            e2e_p95_ms=e2e.get("p95_ms"),
            throughput_toks=toks,
        ))
    return results


def load_quality(path: Path) -> list[QualityMetrics]:
    """Parse fy-quality JSON."""
    data = _read_json(path)
    by_channel: dict[str, dict] = {}
    for item in data.get("per_prompt", data.get("results", [])):
        ch = item.get("channel", item.get("channel_name", item.get("source_name", "unknown")))
        if ch not in by_channel:
            by_channel[ch] = {"passed": 0, "total": 0, "scores": [], "model": "", "channel_id": None}
        by_channel[ch]["total"] += 1
        if item.get("passed"):
            by_channel[ch]["passed"] += 1
        if "score" in item:
            by_channel[ch]["scores"].append(item["score"])
        if not by_channel[ch]["model"]:
            by_channel[ch]["model"] = item.get("model", "")
        if by_channel[ch]["channel_id"] is None:
            by_channel[ch]["channel_id"] = item.get("channel_id", item.get("pin_channel_id"))

    results: list[QualityMetrics] = []
    for ch_name, info in by_channel.items():
        total = info["total"]
        pass_rate = info["passed"] / total if total > 0 else 0.0
        scores = info["scores"]
        avg_score = sum(scores) / len(scores) if scores else pass_rate
        results.append(QualityMetrics(
            channel_name=ch_name,
            channel_id=info["channel_id"],
            model=info["model"],
            pass_rate=pass_rate,
            avg_score=avg_score,
        ))
    return results


def load_canary(path: Path) -> list[CanaryMetrics]:
    """Parse fy-canary JSON."""
    data = _read_json(path)
    outcomes = data.get("outcomes", [])
    if not outcomes:
        return []
    passed = sum(1 for o in outcomes if o.get("passed"))
    total = len(outcomes)
    scores = [o.get("score", 0.0) for o in outcomes]
    source_name = data.get("source_name", "")
    return [CanaryMetrics(
        channel_name=source_name,
        channel_id=_extract_channel_id(source_name),
        model=data.get("model", ""),
        probe_pass_rate=passed / total if total > 0 else 0.0,
        avg_probe_score=sum(scores) / len(scores) if scores else 0.0,
    )]


def load_conformance(path: Path) -> list[ConformanceMetrics]:
    """Parse fy-conformance summary.json. Model extracted from filename."""
    data = _read_json(path)
    pass_rate = data.get("pass_rate", 0.0)
    # Filename pattern: conformance-{model}-{timestamp}.summary.json
    m = re.match(r"conformance-(.+?)-\d{8}T\d{6}Z", path.stem.replace(".summary", ""))
    model = m.group(1) if m else ""
    return [ConformanceMetrics(
        channel_name="", channel_id=None, model=model, pass_rate=pass_rate,
    )]


def load_integrity(path: Path) -> list[IntegrityMetrics]:
    """Parse fy-integrity JSON. Extracts probe list + channel/model from config."""
    data = _read_json(path)
    cfg = data.get("config", {})
    model = cfg.get("model", "")
    channel_id = cfg.get("pin_channel_id")
    probes = data.get("probes", [])
    return [IntegrityMetrics(
        channel_name=f"channel-{channel_id}" if channel_id else "",
        channel_id=channel_id,
        model=model,
        probes=probes,
    )]


@dataclass
class ImageCanaryMetrics:
    channel_name: str
    channel_id: int | None
    model: str
    probe_pass_rate: float
    avg_probe_score: float
    combined_verdict: str


def load_image_canary(path: Path) -> list[ImageCanaryMetrics]:
    """Parse fy-image-canary JSON."""
    data = _read_json(path)
    outcomes = data.get("outcomes", [])
    if not outcomes:
        return []
    passed = sum(1 for o in outcomes if o.get("passed"))
    total = len(outcomes)
    scores = [o.get("score", 0.0) for o in outcomes]
    channel_name = data.get("channel_name", "")
    return [ImageCanaryMetrics(
        channel_name=channel_name,
        channel_id=_extract_channel_id(channel_name),
        model=data.get("model", ""),
        probe_pass_rate=passed / total if total > 0 else 0.0,
        avg_probe_score=sum(scores) / len(scores) if scores else 0.0,
        combined_verdict=data.get("combined_verdict", ""),
    )]


@dataclass
class ImageConformanceMetrics:
    channel_name: str
    channel_id: int | None
    model: str
    api_compat_pass_rate: float
    output_valid_pass_rate: float
    safety_pass_rate: float
    zh_pass_rate: float | None
    en_pass_rate: float | None
    phase_a_blocked: bool
    p50_ms: float | None
    p95_ms: float | None
    rpm: float | None
    success_rate: float | None


def load_image_conformance(path: Path) -> list[ImageConformanceMetrics]:
    """Parse fy-image-conformance JSON report."""
    data = _read_json(path)
    if not data:
        return []

    model = data.get("model", "")
    results = []

    for ch_data in data.get("channels", [data]):
        ch_name = ch_data.get("channel_name", ch_data.get("channel", ""))
        ch_id = ch_data.get("channel_id") or _extract_channel_id(ch_name)

        compat = ch_data.get("api_compat", {})
        output = ch_data.get("output_valid", {})
        safety = ch_data.get("safety", {})
        prompt = ch_data.get("prompt_follow", {})
        perf = ch_data.get("perf", {})

        results.append(ImageConformanceMetrics(
            channel_name=ch_name,
            channel_id=ch_id,
            model=model or ch_data.get("model", ""),
            api_compat_pass_rate=float(compat.get("pass_rate", 0.0)),
            output_valid_pass_rate=float(output.get("pass_rate", 0.0)),
            safety_pass_rate=float(safety.get("pass_rate", 0.0)),
            zh_pass_rate=prompt.get("zh_pass_rate"),
            en_pass_rate=prompt.get("en_pass_rate"),
            phase_a_blocked=bool(prompt.get("phase_a_blocked", False)),
            p50_ms=perf.get("p50_ms"),
            p95_ms=perf.get("p95_ms"),
            rpm=perf.get("rpm"),
            success_rate=perf.get("success_rate"),
        ))
    return results


@dataclass
class ImageLoadtestMetrics:
    channel_name: str
    channel_id: int | None
    model: str
    p50_ms: float | None
    p95_ms: float | None
    rpm: float | None
    success_rate: float | None


def load_image_loadtest(path: Path) -> list[ImageLoadtestMetrics]:
    """Parse fy-image-loadtest JSON report."""
    data = _read_json(path)
    results = []
    for ch in data.get("channels", []):
        ch_name = ch.get("channel_name", ch.get("name", ""))
        ch_id = ch.get("pin_channel_id") or _extract_channel_id(ch_name)
        stats = ch.get("stats", ch)
        p50 = stats.get("e2e_p50_ms", stats.get("p50_ms"))
        p95 = stats.get("e2e_p95_ms", stats.get("p95_ms"))
        if p50 is None:
            warnings.warn(f"Missing e2e_p50_ms and p50_ms in image loadtest for {ch_name}")
        if p95 is None:
            warnings.warn(f"Missing e2e_p95_ms and p95_ms in image loadtest for {ch_name}")
        results.append(ImageLoadtestMetrics(
            channel_name=ch_name,
            channel_id=ch_id,
            model=data.get("model", ch.get("model", "")),
            p50_ms=p50,
            p95_ms=p95,
            rpm=stats.get("rpm"),
            success_rate=stats.get("success_rate"),
        ))
    return results

