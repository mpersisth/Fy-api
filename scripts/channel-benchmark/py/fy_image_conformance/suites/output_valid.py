"""Layer 2: Output validation — verify returned images are correct.

Includes: format detection, dimension parsing, size consistency, and dedup detection.
"""

from __future__ import annotations

import base64
import hashlib
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

        # Basic generation + validation
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

        # Size consistency check
        requested_size = cfg.model.supported_sizes[0] if cfg.model.supported_sizes else None
        if requested_size:
            cr.cases.extend(
                await _check_size_consistency(client, cfg, ch, requested_size))

        # Dedup check
        cr.cases.extend(await _check_dedup(client, cfg, ch))

        results.append(cr)
    return results


async def _check_size_consistency(
    client: ImageClient,
    cfg: Config,
    ch: ChannelTarget,
    requested_size: str,
) -> list[ValidationCase]:
    try:
        w_req, h_req = (int(x) for x in requested_size.split("x"))
    except ValueError:
        return []

    body = {
        "model": cfg.model.name,
        "prompt": cfg.model.default_prompt,
        "n": 1,
        "size": requested_size,
    }
    r = await client.generate(body, pin_channel=ch.pin_channel_id)
    if not r.success:
        return [ValidationCase("size_consistency", False, f"generation failed: {r.error[:80]}")]

    image_data = await _get_image_bytes(client, r)
    if not image_data:
        return [ValidationCase("size_consistency", False, "could not retrieve image")]

    fmt = _detect_format(image_data)
    dims = _detect_dimensions(image_data, fmt)
    if not dims:
        return [ValidationCase("size_consistency", False, f"could not detect dimensions (format={fmt})")]

    w_actual, h_actual = dims
    match = (w_actual == w_req and h_actual == h_req)
    return [ValidationCase(
        "size_consistency", match,
        f"requested={w_req}x{h_req} actual={w_actual}x{h_actual}",
    )]


async def _check_dedup(
    client: ImageClient,
    cfg: Config,
    ch: ChannelTarget,
    n_generations: int = 3,
) -> list[ValidationCase]:
    body = {"model": cfg.model.name, "prompt": cfg.model.default_prompt, "n": 1}
    hashes: list[str] = []

    for _ in range(n_generations):
        r = await client.generate(dict(body), pin_channel=ch.pin_channel_id)
        if not r.success:
            continue
        image_data = await _get_image_bytes(client, r)
        if image_data:
            hashes.append(hashlib.md5(image_data).hexdigest())

    if len(hashes) < 2:
        return [ValidationCase("dedup_check", True, f"only {len(hashes)} images generated, skipped")]

    unique = len(set(hashes))
    total = len(hashes)
    dup_rate = 1.0 - (unique / total)
    ok = dup_rate < 0.5
    return [ValidationCase(
        "dedup_check", ok,
        f"{unique}/{total} unique images (dup_rate={dup_rate:.0%})",
    )]


async def _get_image_bytes(client: ImageClient, r: ImageResult) -> bytes:
    if r.image_b64:
        try:
            return base64.b64decode(r.image_b64[0])
        except Exception:
            return b""
    if r.image_urls:
        try:
            data, _ = await client.download_image(r.image_urls[0])
            return data
        except Exception:
            return b""
    return b""


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
    if fmt == "webp":
        return _webp_dimensions(data)
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


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30:
        return None
    if data[12:16] == b"VP8 " and len(data) > 29:
        w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return (w, h)
    if data[12:16] == b"VP8L" and len(data) > 25:
        bits = struct.unpack("<I", data[21:25])[0]
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
        return (w, h)
    return None
