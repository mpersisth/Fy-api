"""HTTP client for OpenAI-compatible image generation API."""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class ImageResult:
    success: bool
    status_code: int
    elapsed_sec: float
    error: str = ""
    image_urls: list[str] = field(default_factory=list)
    image_b64: list[str] = field(default_factory=list)
    response_body: dict | None = None
    content_type: str = ""
    revised_prompt: str = ""


class ImageClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 300.0):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout, connect=30.0),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> ImageClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def generate(
        self,
        body: dict,
        *,
        pin_channel: int | None = None,
    ) -> ImageResult:
        headers = {}
        if pin_channel is not None:
            headers["X-Oneapi-Channel"] = str(pin_channel)

        t0 = time.perf_counter()
        try:
            resp = await self._http.post(
                "/v1/images/generations",
                json=body,
                headers=headers,
            )
            elapsed = time.perf_counter() - t0
        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
        ) as e:
            return ImageResult(
                success=False,
                status_code=0,
                elapsed_sec=time.perf_counter() - t0,
                error=f"connection error: {type(e).__name__}: {e}",
            )

        if resp.status_code != 200:
            err_text = resp.text[:500]
            return ImageResult(
                success=False,
                status_code=resp.status_code,
                elapsed_sec=elapsed,
                error=err_text,
                response_body=_safe_json(resp),
            )

        data = resp.json()
        urls = [d.get("url", "") for d in data.get("data", []) if d.get("url")]
        b64s = [d.get("b64_json", "") for d in data.get("data", []) if d.get("b64_json")]
        revised = data.get("data", [{}])[0].get("revised_prompt", "") if data.get("data") else ""

        return ImageResult(
            success=True,
            status_code=200,
            elapsed_sec=elapsed,
            image_urls=urls,
            image_b64=b64s,
            response_body=data,
            revised_prompt=revised,
        )

    async def download_image(self, url: str) -> tuple[bytes, str]:
        resp = await self._http.get(url)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        return resp.content, ct


def _safe_json(resp: httpx.Response) -> dict | None:
    try:
        return resp.json()
    except Exception:
        return None
