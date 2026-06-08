"""Threshold calibration for image canary CLIP/histogram comparisons.

Runs N generations through the same channel for each prompt, then computes
pairwise CLIP cosine and color histogram correlation distributions.
Outputs recommended thresholds (mean - 3σ).
"""

from __future__ import annotations

import json
import math
import datetime
from dataclasses import dataclass, field
from pathlib import Path

from fy_image_conformance.client import ImageClient

from .client import generate_and_download, ImageSample
from .comparators.clip import clip_available, compute_clip_embedding, _cosine
from .comparators.histogram import pillow_available, compute_rgb_histogram, _pearson_correlation
from .config import ImageCanaryConfig


@dataclass
class CalibrationResult:
    prompt: str
    n_pairs: int
    clip_cosines: list[float] = field(default_factory=list)
    color_correlations: list[float] = field(default_factory=list)

    @property
    def clip_mean(self) -> float:
        return sum(self.clip_cosines) / len(self.clip_cosines) if self.clip_cosines else 0.0

    @property
    def clip_std(self) -> float:
        return _std(self.clip_cosines)

    @property
    def color_mean(self) -> float:
        return sum(self.color_correlations) / len(self.color_correlations) if self.color_correlations else 0.0

    @property
    def color_std(self) -> float:
        return _std(self.color_correlations)


@dataclass
class CalibrationReport:
    model: str
    channel: str
    n_per_prompt: int
    results: list[CalibrationResult] = field(default_factory=list)
    recommended_clip_threshold: float = 0.0
    recommended_color_threshold: float = 0.0


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    return math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))


async def run_calibration(
    cfg: ImageCanaryConfig,
    n_per_prompt: int = 10,
) -> CalibrationReport:
    report = CalibrationReport(
        model=cfg.gateway.model,
        channel=cfg.gateway.name,
        n_per_prompt=n_per_prompt,
    )

    use_clip = clip_available()
    use_hist = pillow_available()

    async with ImageClient(
        cfg.gateway.base_url,
        cfg.gateway.user_token,
        timeout=cfg.request_timeout_sec,
    ) as client:
        for prompt in cfg.test_prompts:
            samples: list[ImageSample] = []
            for _ in range(n_per_prompt):
                body = {"model": cfg.gateway.model, "prompt": prompt, "n": 1}
                s = await generate_and_download(
                    client, body, pin_channel=cfg.gateway.pin_channel_id)
                if s.success and s.image_bytes:
                    samples.append(s)

            cr = CalibrationResult(prompt=prompt, n_pairs=0)

            if len(samples) < 2:
                report.results.append(cr)
                continue

            clip_embeddings = []
            histograms = []
            if use_clip:
                for s in samples:
                    try:
                        clip_embeddings.append(compute_clip_embedding(s.image_bytes))
                    except Exception:
                        clip_embeddings.append(None)

            if use_hist:
                for s in samples:
                    try:
                        histograms.append(compute_rgb_histogram(s.image_bytes))
                    except Exception:
                        histograms.append(None)

            n_pairs = 0
            for i in range(len(samples)):
                for j in range(i + 1, len(samples)):
                    n_pairs += 1
                    if use_clip and clip_embeddings[i] and clip_embeddings[j]:
                        cr.clip_cosines.append(
                            _cosine(clip_embeddings[i], clip_embeddings[j]))
                    if use_hist and histograms[i] and histograms[j]:
                        cr.color_correlations.append(
                            _pearson_correlation(histograms[i], histograms[j]))

            cr.n_pairs = n_pairs
            report.results.append(cr)

    all_clip = [v for r in report.results for v in r.clip_cosines]
    all_color = [v for r in report.results for v in r.color_correlations]

    if all_clip:
        mean = sum(all_clip) / len(all_clip)
        std = _std(all_clip)
        report.recommended_clip_threshold = round(max(0.0, mean - 3 * std), 4)
    if all_color:
        mean = sum(all_color) / len(all_color)
        std = _std(all_color)
        report.recommended_color_threshold = round(max(0.0, mean - 3 * std), 4)

    return report


def save_calibration(report: CalibrationReport, output_dir: str) -> str:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    model = report.model.replace("/", "_")
    filename = f"calibration-{model}-{now}.json"
    filepath = path / filename

    data = {
        "model": report.model,
        "channel": report.channel,
        "n_per_prompt": report.n_per_prompt,
        "recommended_clip_threshold": report.recommended_clip_threshold,
        "recommended_color_threshold": report.recommended_color_threshold,
        "per_prompt": [
            {
                "prompt": r.prompt,
                "n_pairs": r.n_pairs,
                "clip_mean": r.clip_mean,
                "clip_std": r.clip_std,
                "color_mean": r.color_mean,
                "color_std": r.color_std,
            }
            for r in report.results
        ],
    }
    filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(filepath)
