"""Image model probe — auto-detect supported models on a channel."""

from __future__ import annotations

import httpx
from rich.console import Console

from ...config import Config

MAINSTREAM_IMAGE_MODELS = [
    "dall-e-3",
    "dall-e-2",
    "gpt-image-1",
    "stable-diffusion-xl",
    "stable-diffusion-3",
    "flux-1",
    "flux-1-pro",
    "wanx-v1",
    "cogview-3",
    "cogview-4",
    "kolors",
    "ideogram",
    "recraft-v3",
]


async def detect(cfg: Config, console: Console) -> list[str]:
    base_url = cfg.channel.base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {cfg.channel.user_token}",
        "Content-Type": "application/json",
    }
    if cfg.channel.pin_channel_id:
        headers["X-Oneapi-Channel"] = str(cfg.channel.pin_channel_id)

    supported = []
    async with httpx.AsyncClient(timeout=60.0) as http:
        for model in MAINSTREAM_IMAGE_MODELS:
            body = {
                "model": model,
                "prompt": "a simple red circle on white background",
                "n": 1,
            }
            try:
                resp = await http.post(
                    f"{base_url}/v1/images/generations",
                    headers=headers, json=body,
                )
                if resp.status_code == 200:
                    supported.append(model)
                    console.print(f"    [green]{model}[/green] supported")
                elif resp.status_code == 429:
                    supported.append(model)
                    console.print(f"    [yellow]{model}[/yellow] rate-limited but exists")
                else:
                    console.print(f"    [dim]{model}[/dim] not available")
            except Exception:
                console.print(f"    [dim]{model}[/dim] timeout")

    return supported
