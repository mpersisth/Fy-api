"""5A — Vendor Direct Comparison runner."""

from __future__ import annotations

from dataclasses import dataclass, field

from fy_image_conformance.client import ImageClient

from .client import ImageSample, generate_and_download
from .config import ImageCanaryConfig
from .comparators.clip import ClipVerdict, clip_available, evaluate_clip
from .comparators.histogram import HistogramVerdict, pillow_available, evaluate_histogram
from .comparators.vlm_judge import VlmComparisonVerdict, evaluate_vlm_comparison
from .verdict import ProbeOutcome, CONFIDENCE_HIGH


@dataclass
class VendorCompareResult:
    prompt: str
    gateway_sample: ImageSample
    vendor_sample: ImageSample
    clip: ClipVerdict | None = None
    histogram: HistogramVerdict | None = None
    vlm: VlmComparisonVerdict | None = None


async def run_5a(
    cfg: ImageCanaryConfig,
    gateway_client: ImageClient,
    vendor_client: ImageClient,
) -> tuple[list[VendorCompareResult], list[ProbeOutcome]]:
    """Run vendor-direct comparison for all test prompts.

    Returns (results, outcomes) where outcomes are ProbeOutcome entries
    ready to be added to the CanaryReport.
    """
    if cfg.vendor is None:
        return [], []

    results: list[VendorCompareResult] = []
    outcomes: list[ProbeOutcome] = []

    judge_base = cfg.judge.base_url or cfg.gateway.base_url
    judge_token = cfg.judge.token or cfg.gateway.user_token

    gw_success = 0
    vendor_success = 0
    gw_total_latency = 0.0
    vendor_total_latency = 0.0

    for i, prompt in enumerate(cfg.test_prompts):
        gw_body = {"model": cfg.gateway.model, "prompt": prompt, "n": 1}
        vendor_body = {"model": cfg.vendor.model, "prompt": prompt, "n": 1}

        gw_sample = await generate_and_download(
            gateway_client, gw_body, pin_channel=cfg.gateway.pin_channel_id)
        vendor_sample = await generate_and_download(vendor_client, vendor_body)

        result = VendorCompareResult(
            prompt=prompt, gateway_sample=gw_sample, vendor_sample=vendor_sample)

        if gw_sample.success:
            gw_success += 1
            gw_total_latency += gw_sample.elapsed_sec
        if vendor_sample.success:
            vendor_success += 1
            vendor_total_latency += vendor_sample.elapsed_sec

        if gw_sample.success and vendor_sample.success:
            gw_bytes = gw_sample.image_bytes
            vendor_bytes = vendor_sample.image_bytes

            if clip_available() and gw_bytes and vendor_bytes:
                try:
                    result.clip = evaluate_clip(
                        prompt=prompt,
                        gateway_image=gw_bytes,
                        vendor_image=vendor_bytes,
                        threshold=cfg.thresholds.clip_cosine_min,
                    )
                    outcomes.append(ProbeOutcome(
                        probe_id=f"5a-clip-{i}",
                        method="clip",
                        passed=result.clip.passed,
                        score=result.clip.cosine_similarity,
                        detail=f"CLIP cosine={result.clip.cosine_similarity:.4f} "
                               f"(threshold={cfg.thresholds.clip_cosine_min})",
                        confidence=CONFIDENCE_HIGH,
                    ))
                except Exception as e:
                    outcomes.append(ProbeOutcome(
                        probe_id=f"5a-clip-{i}", method="clip",
                        passed=False, score=0.0,
                        detail=f"CLIP error: {e}",
                    ))

            if pillow_available() and gw_bytes and vendor_bytes:
                try:
                    result.histogram = evaluate_histogram(
                        prompt=prompt,
                        gateway_image=gw_bytes,
                        vendor_image=vendor_bytes,
                        threshold=cfg.thresholds.color_correlation_min,
                    )
                    outcomes.append(ProbeOutcome(
                        probe_id=f"5a-histogram-{i}",
                        method="color_histogram",
                        passed=result.histogram.passed,
                        score=result.histogram.correlation,
                        detail=f"color corr={result.histogram.correlation:.4f} "
                               f"(threshold={cfg.thresholds.color_correlation_min})",
                        confidence=CONFIDENCE_HIGH,
                    ))
                except Exception as e:
                    outcomes.append(ProbeOutcome(
                        probe_id=f"5a-histogram-{i}", method="color_histogram",
                        passed=False, score=0.0,
                        detail=f"histogram error: {e}",
                    ))

            if gw_sample.image_b64 and vendor_sample.image_b64:
                try:
                    result.vlm = await evaluate_vlm_comparison(
                        prompt=prompt,
                        gateway_image_b64=gw_sample.image_b64,
                        vendor_image_b64=vendor_sample.image_b64,
                        judge_base_url=judge_base,
                        judge_token=judge_token,
                        judge_model=cfg.judge.model,
                        repeat=cfg.judge.repeat,
                    )
                    outcomes.append(ProbeOutcome(
                        probe_id=f"5a-vlm-{i}",
                        method="vlm_comparison",
                        passed=result.vlm.passed,
                        score=1.0 if result.vlm.passed else 0.0,
                        detail=f"VLM verdict={result.vlm.judge_verdict}: "
                               f"{result.vlm.reasoning[:100]}",
                        confidence=result.vlm.confidence,
                    ))
                except Exception as e:
                    outcomes.append(ProbeOutcome(
                        probe_id=f"5a-vlm-{i}", method="vlm_comparison",
                        passed=False, score=0.0,
                        detail=f"VLM error: {e}",
                    ))
        else:
            detail_parts = []
            if not gw_sample.success:
                detail_parts.append(f"gateway failed: {gw_sample.error[:80]}")
            if not vendor_sample.success:
                detail_parts.append(f"vendor failed: {vendor_sample.error[:80]}")
            outcomes.append(ProbeOutcome(
                probe_id=f"5a-gen-{i}", method="generation",
                passed=False, score=0.0,
                detail="; ".join(detail_parts),
            ))

        results.append(result)

    n = len(cfg.test_prompts)
    if n > 0:
        gw_rate = gw_success / n
        vendor_rate = vendor_success / n
        rate_diff = abs(gw_rate - vendor_rate)
        outcomes.append(ProbeOutcome(
            probe_id="5a-success-rate",
            method="success_rate",
            passed=rate_diff <= cfg.thresholds.success_rate_diff_max,
            score=1.0 - rate_diff,
            detail=f"gateway={gw_rate:.0%} vendor={vendor_rate:.0%} diff={rate_diff:.0%}",
        ))

        if vendor_success > 0 and gw_success > 0:
            avg_gw = gw_total_latency / gw_success
            avg_vendor = vendor_total_latency / vendor_success
            ratio = avg_gw / avg_vendor if avg_vendor > 0 else 999.0
            outcomes.append(ProbeOutcome(
                probe_id="5a-latency",
                method="latency_regression",
                passed=ratio <= cfg.thresholds.latency_ratio_max,
                score=max(0.0, 1.0 - (ratio - 1.0)),
                detail=f"gateway_avg={avg_gw:.1f}s vendor_avg={avg_vendor:.1f}s ratio={ratio:.2f}×",
            ))

    return results, outcomes
