"""5B-2: Cross-channel comparison.

Same model on 2+ channels. Generate with same prompt, compare via CLIP cosine.
"""

from __future__ import annotations

from dataclasses import dataclass

from fy_image_conformance.client import ImageClient
from ..client import ImageSample, generate_and_download
from ..config import GatewayTarget
from ..comparators.clip import clip_available, evaluate_clip
from ..verdict import ProbeOutcome, CONFIDENCE_HIGH


@dataclass
class CrossChannelVerdict:
    prompt: str
    channel_a: str
    channel_b: str
    clip_cosine: float | None
    passed: bool
    detail: str


async def run_cross_channel(
    clients: dict[str, ImageClient],
    channels: list[GatewayTarget],
    model: str,
    prompts: list[str],
    *,
    existing_samples: dict[str, dict[str, ImageSample]] | None = None,
    clip_threshold: float = 0.90,
) -> tuple[list[CrossChannelVerdict], list[ProbeOutcome]]:
    """Compare images across channels for the same prompt.

    existing_samples: {channel_name: {prompt: ImageSample}} — reuse from 5A if available.
    Returns (verdicts, outcomes).
    """
    if len(channels) < 2:
        return [], []
    if not clip_available():
        return [], [ProbeOutcome(
            probe_id="5b2-skip", method="cross_channel",
            passed=True, score=0.5,
            detail="CLIP not available — install [image-canary] extras",
            confidence="low",
        )]

    verdicts: list[CrossChannelVerdict] = []
    outcomes: list[ProbeOutcome] = []

    ch_a = channels[0]
    existing = existing_samples or {}

    for i, prompt in enumerate(prompts):
        sample_a = (existing.get(ch_a.name) or {}).get(prompt)
        if sample_a is None and ch_a.name in clients:
            body = {"model": model, "prompt": prompt, "n": 1}
            sample_a = await generate_and_download(
                clients[ch_a.name], body, pin_channel=ch_a.pin_channel_id)

        if sample_a is None or not sample_a.success:
            continue

        for ch_b in channels[1:]:
            sample_b = (existing.get(ch_b.name) or {}).get(prompt)
            if sample_b is None and ch_b.name in clients:
                body = {"model": model, "prompt": prompt, "n": 1}
                sample_b = await generate_and_download(
                    clients[ch_b.name], body, pin_channel=ch_b.pin_channel_id)

            if sample_b is None or not sample_b.success:
                continue

            if sample_a.image_bytes and sample_b.image_bytes:
                try:
                    clip_result = evaluate_clip(
                        prompt=prompt,
                        gateway_image=sample_a.image_bytes,
                        vendor_image=sample_b.image_bytes,
                        threshold=clip_threshold,
                    )
                    v = CrossChannelVerdict(
                        prompt=prompt, channel_a=ch_a.name, channel_b=ch_b.name,
                        clip_cosine=clip_result.cosine_similarity,
                        passed=clip_result.passed,
                        detail=f"CLIP cosine={clip_result.cosine_similarity:.4f}",
                    )
                    verdicts.append(v)
                    outcomes.append(ProbeOutcome(
                        probe_id=f"5b2-{ch_a.name}-{ch_b.name}-{i}",
                        method="cross_channel",
                        passed=v.passed,
                        score=clip_result.cosine_similarity,
                        detail=f"{ch_a.name} vs {ch_b.name}: {v.detail}",
                        confidence=CONFIDENCE_HIGH,
                    ))
                except Exception as e:
                    outcomes.append(ProbeOutcome(
                        probe_id=f"5b2-{ch_a.name}-{ch_b.name}-{i}",
                        method="cross_channel",
                        passed=False, score=0.0,
                        detail=f"CLIP comparison error: {e}",
                    ))

    return verdicts, outcomes
