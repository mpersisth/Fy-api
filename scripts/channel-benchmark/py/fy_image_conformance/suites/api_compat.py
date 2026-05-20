"""Layer 1: API compatibility — verify the channel implements the image API correctly."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ..client import ImageClient, ImageResult
from ..config import Config, ChannelTarget


@dataclass
class CaseResult:
    name: str
    passed: bool
    detail: str = ""
    elapsed_sec: float = 0.0


@dataclass
class ChannelCompatResult:
    channel: ChannelTarget
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.cases if not c.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / len(self.cases) if self.cases else 0.0


async def run(cfg: Config, client: ImageClient) -> list[ChannelCompatResult]:
    results = []
    for ch in cfg.gateway.channels:
        cr = ChannelCompatResult(channel=ch)
        cr.cases.append(await _test_basic_generation(cfg, client, ch))
        cr.cases.extend(await _test_sizes(cfg, client, ch))
        cr.cases.extend(await _test_qualities(cfg, client, ch))
        cr.cases.extend(await _test_formats(cfg, client, ch))
        if cfg.model.supports_n_gt_1:
            cr.cases.append(await _test_n_gt_1(cfg, client, ch))
        cr.cases.append(await _test_response_format_b64(cfg, client, ch))
        results.append(cr)
    return results


async def _test_basic_generation(
    cfg: Config, client: ImageClient, ch: ChannelTarget
) -> CaseResult:
    body = {"model": cfg.model.name, "prompt": cfg.model.default_prompt, "n": 1}
    r = await client.generate(body, pin_channel=ch.pin_channel_id)
    if r.success:
        return CaseResult("basic_generation", True, "OK", r.elapsed_sec)
    return CaseResult("basic_generation", False, r.error[:200], r.elapsed_sec)


async def _test_sizes(
    cfg: Config, client: ImageClient, ch: ChannelTarget
) -> list[CaseResult]:
    results = []
    for size in cfg.model.supported_sizes:
        body = {
            "model": cfg.model.name,
            "prompt": cfg.model.default_prompt,
            "size": size,
            "n": 1,
        }
        r = await client.generate(body, pin_channel=ch.pin_channel_id)
        name = f"size_{size}"
        if r.success:
            results.append(CaseResult(name, True, "OK", r.elapsed_sec))
        else:
            results.append(CaseResult(name, False, r.error[:200], r.elapsed_sec))
    return results


async def _test_qualities(
    cfg: Config, client: ImageClient, ch: ChannelTarget
) -> list[CaseResult]:
    results = []
    for q in cfg.model.supported_qualities:
        body = {
            "model": cfg.model.name,
            "prompt": cfg.model.default_prompt,
            "quality": q,
            "n": 1,
        }
        r = await client.generate(body, pin_channel=ch.pin_channel_id)
        name = f"quality_{q}"
        if r.success:
            results.append(CaseResult(name, True, "OK", r.elapsed_sec))
        else:
            results.append(CaseResult(name, False, r.error[:200], r.elapsed_sec))
    return results


async def _test_formats(
    cfg: Config, client: ImageClient, ch: ChannelTarget
) -> list[CaseResult]:
    results = []
    for fmt in cfg.model.supported_formats:
        body = {
            "model": cfg.model.name,
            "prompt": cfg.model.default_prompt,
            "output_format": fmt,
            "n": 1,
        }
        r = await client.generate(body, pin_channel=ch.pin_channel_id)
        name = f"format_{fmt}"
        if r.success:
            results.append(CaseResult(name, True, "OK", r.elapsed_sec))
        else:
            results.append(CaseResult(name, False, r.error[:200], r.elapsed_sec))
    return results


async def _test_n_gt_1(
    cfg: Config, client: ImageClient, ch: ChannelTarget
) -> CaseResult:
    n = min(cfg.model.max_n, 2)
    body = {
        "model": cfg.model.name,
        "prompt": cfg.model.default_prompt,
        "n": n,
    }
    r = await client.generate(body, pin_channel=ch.pin_channel_id)
    if not r.success:
        return CaseResult(f"n={n}", False, r.error[:200], r.elapsed_sec)
    count = len(r.image_urls) + len(r.image_b64)
    if count < n:
        return CaseResult(f"n={n}", False, f"expected {n} images, got {count}", r.elapsed_sec)
    return CaseResult(f"n={n}", True, f"returned {count} images", r.elapsed_sec)


async def _test_response_format_b64(
    cfg: Config, client: ImageClient, ch: ChannelTarget
) -> CaseResult:
    body = {
        "model": cfg.model.name,
        "prompt": cfg.model.default_prompt,
        "response_format": "b64_json",
        "n": 1,
    }
    r = await client.generate(body, pin_channel=ch.pin_channel_id)
    if not r.success:
        return CaseResult("response_format_b64", False, r.error[:200], r.elapsed_sec)
    if not r.image_b64:
        return CaseResult("response_format_b64", False, "no b64_json in response", r.elapsed_sec)
    return CaseResult("response_format_b64", True, "OK", r.elapsed_sec)
