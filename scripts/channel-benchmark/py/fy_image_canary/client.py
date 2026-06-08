"""Image sample generation and download for canary detection."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from fy_image_conformance.client import ImageClient, ImageResult


@dataclass
class ImageSample:
    prompt: str
    image_bytes: bytes
    image_b64: str
    elapsed_sec: float
    success: bool
    error: str = ""
    revised_prompt: str = ""
    response_body: dict | None = None


async def generate_and_download(
    client: ImageClient,
    body: dict,
    *,
    pin_channel: int | None = None,
) -> ImageSample:
    r = await client.generate(body, pin_channel=pin_channel)
    if not r.success:
        return ImageSample(
            prompt=body.get("prompt", ""),
            image_bytes=b"",
            image_b64="",
            elapsed_sec=r.elapsed_sec,
            success=False,
            error=r.error[:300],
            response_body=r.response_body,
        )

    image_bytes = b""
    image_b64 = ""

    if r.image_b64:
        image_b64 = r.image_b64[0]
        try:
            image_bytes = base64.b64decode(image_b64)
        except Exception:
            pass
    elif r.image_urls:
        try:
            image_bytes, _ = await client.download_image(r.image_urls[0])
            image_b64 = base64.b64encode(image_bytes).decode()
        except Exception as e:
            return ImageSample(
                prompt=body.get("prompt", ""),
                image_bytes=b"",
                image_b64="",
                elapsed_sec=r.elapsed_sec,
                success=False,
                error=f"download failed: {e}",
                response_body=r.response_body,
            )

    return ImageSample(
        prompt=body.get("prompt", ""),
        image_bytes=image_bytes,
        image_b64=image_b64,
        elapsed_sec=r.elapsed_sec,
        success=True,
        revised_prompt=r.revised_prompt,
        response_body=r.response_body,
    )
