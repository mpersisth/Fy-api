"""RGB color histogram correlation comparison using Pillow."""

from __future__ import annotations

import math
from dataclasses import dataclass


def pillow_available() -> bool:
    try:
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class HistogramVerdict:
    prompt: str
    correlation: float
    threshold: float
    passed: bool


def compute_rgb_histogram(image_bytes: bytes, bins: int = 64) -> list[float]:
    import io
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    r, g, b = image.split()
    hist: list[float] = []
    for channel in (r, g, b):
        raw = channel.histogram()
        step = 256 // bins
        binned = [sum(raw[i * step:(i + 1) * step]) for i in range(bins)]
        total = sum(binned) or 1
        hist.extend(v / total for v in binned)
    return hist


def _pearson_correlation(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n == 0:
        return 0.0
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    std_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    std_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    if std_a == 0 or std_b == 0:
        return 0.0
    return cov / (std_a * std_b)


def evaluate_histogram(
    *,
    prompt: str,
    gateway_image: bytes,
    vendor_image: bytes,
    threshold: float = 0.85,
) -> HistogramVerdict:
    hist_gw = compute_rgb_histogram(gateway_image)
    hist_vendor = compute_rgb_histogram(vendor_image)
    corr = _pearson_correlation(hist_gw, hist_vendor)
    return HistogramVerdict(
        prompt=prompt,
        correlation=corr,
        threshold=threshold,
        passed=corr >= threshold,
    )
