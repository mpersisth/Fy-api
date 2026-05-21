"""Auto-detect which mainstream image models a channel supports."""

from __future__ import annotations

from dataclasses import dataclass

from .client import ImageClient
from .config import ChannelTarget

MAINSTREAM_IMAGE_MODELS = [
    "dall-e-3",
    "dall-e-2",
    "gpt-image-1",
    "stable-diffusion-xl",
    "stable-diffusion-3",
    "flux-1",
    "flux-1-pro",
    "midjourney",
    "wanx-v1",
    "cogview-3",
    "cogview-4",
    "kolors",
    "ideogram",
    "recraft-v3",
    "playground-v2.5",
]


@dataclass
class ProbeResult:
    model: str
    supported: bool
    status_code: int = 0
    detail: str = ""


async def probe_channel(
    client: ImageClient,
    channel: ChannelTarget,
    models: list[str] | None = None,
) -> list[ProbeResult]:
    targets = models or MAINSTREAM_IMAGE_MODELS
    results = []
    for model in targets:
        body = {
            "model": model,
            "prompt": "a simple red circle on white background",
            "n": 1,
        }
        r = await client.generate(body, pin_channel=channel.pin_channel_id)
        if r.success:
            results.append(ProbeResult(model, True, 200, "OK"))
        elif r.status_code in (400, 404) and _is_model_not_found(r.error):
            results.append(ProbeResult(model, False, r.status_code, "model not available"))
        elif r.status_code == 429:
            results.append(ProbeResult(model, True, 429, "rate limited but model exists"))
        else:
            results.append(ProbeResult(model, False, r.status_code, r.error[:100]))
    return results


def _is_model_not_found(error: str) -> bool:
    keywords = ["model not found", "not exist", "not supported", "invalid model", "unknown model"]
    lower = error.lower()
    return any(k in lower for k in keywords)
