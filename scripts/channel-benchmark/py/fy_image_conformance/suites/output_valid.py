"""Layer 2: Output validation — verify returned images are correct."""

from __future__ import annotations

import base64
import io
import struct
from dataclasses import dataclass, field

from ..client import ImageClient, ImageResult
from ..config import Config, ChannelTarget


@dataclass
class ValidationCase:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ChannelOutputResult:
    channel: ChannelTarget
    cases: list[ValidationCase] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.cases if not c.passed)


async def run(cfg: Config, client: ImageClient) -> list[ChannelOutputResult]:
    results = []
    for ch in cfg.gateway.channels:
        cr = ChannelOutputResult(channel=ch)

        body = {"model": cfg.model.name, "prompt": cfg.model.default_prompt, "n": 1}
        r = await client.generate(body, pin_channel=ch.pin_channel_id)
        if not r.success:
            cr.cases.append(ValidationCase("generate_for_validation", False, r.error[:200]))
            results.append(cr)
            continue

        if r.image_urls:
            cr.cases.extend(await _validate_url(client, r.image_urls[0], cfg))
        elif r.image_b64:
            cr.cases.extend(_validate_b64(r.image_b64[0], cfg))
        else:
            cr.cases.append(ValidationCase("has_image_data", False, "no url or b64 in response"))

        results.append(cr)
    return results


async def _validate_url(
    client: ImageClient, url: str, cfg: Config
) -> list[ValidationCase]:
    cases = []
    cases.append(ValidationCase("has_url", True, url[:100]))
    try:
        data, ct = await client.download_image(url)
    except Exception as e:
        cases.append(ValidationCase("url_accessible", False, str(e)[:200]))
        return cases

    cases.append(ValidationCase("url_accessible", True, f"{len(data)} bytes"))
    cases.extend(_validate_image_bytes(data, cfg))
    return cases


def _validate_b64(b64_str: str, cfg: Config) -> list[ValidationCase]:
    cases = []
    try:
        data = base64.b64decode(b64_str)
    except Exception as e:
        cases.append(ValidationCase("b64_decodable", False, str(e)[:200]))
        return cases

    cases.append(ValidationCase("b64_decodable", True, f"{len(data)} bytes"))
    cases.extend(_validate_image_bytes(data, cfg))
    return cases


def _validate_image_bytes(data: bytes, cfg: Config) -> list[ValidationCase]:
    cases = []
    if len(data) < 1000:
        cases.append(ValidationCase("min_file_size", False, f"only {len(data)} bytes"))
    else:
        cases.append(ValidationCase("min_file_size", True, f"{len(data)} bytes"))

    fmt = _detect_format(data)
    if fmt:
        cases.append(ValidationCase("valid_image_format", True, fmt))
    else:
        cases.append(ValidationCase("valid_image_format", False, "unknown format"))

    dims = _detect_dimensions(data, fmt)
    if dims:
        w, h = dims
        cases.append(ValidationCase("has_dimensions", True, f"{w}x{h}"))
    return cases


def _detect_format(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:2] == b"\xff\xd8":
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def _detect_dimensions(data: bytes, fmt: str) -> tuple[int, int] | None:
    if fmt == "png" and len(data) > 24:
        w = struct.unpack(">I", data[16:20])[0]
        h = struct.unpack(">I", data[20:24])[0]
        return (w, h)
    if fmt == "jpeg":
        return _jpeg_dimensions(data)
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker in (0xC0, 0xC2):
            h = struct.unpack(">H", data[i + 5 : i + 7])[0]
            w = struct.unpack(">H", data[i + 7 : i + 9])[0]
            return (w, h)
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        i += 2 + length
    return None
