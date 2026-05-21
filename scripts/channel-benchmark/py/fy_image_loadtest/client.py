"""Async image-generation client for Fy-api /v1/images/generations."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx

_RETRY_AFTER_RE = re.compile(r"retry after (\d+) seconds", re.IGNORECASE)
_INSUFFICIENT_QUOTA_PATTERNS = (
    "余额不足",
    "额度不足",
    "insufficient quota",
    "quota is not enough",
    "user quota is not enough",
    "account balance is insufficient",
)


@dataclass
class ImageResult:
    success: bool = False
    http_status: int = 0
    error: str = ""
    e2e_s: float = 0.0
    images: int = 0
    response_bytes: int = 0
    has_b64_json: bool = False
    has_url: bool = False
    revised_prompt_count: int = 0
    retry_after_s: float = 0.0
    insufficient_quota: bool = False


class ImageClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        request_timeout: float = 300.0,
        pin_channel_id: int,
    ):
        self._url = base_url.rstrip("/") + "/v1/images/generations"
        effective_token = f"{token}-{pin_channel_id}"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=request_timeout,
                write=30.0,
                pool=10.0,
            ),
            headers={
                "Authorization": f"Bearer {effective_token}",
                "Content-Type": "application/json",
            },
            http2=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> ImageClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def generate(self, body: dict[str, object]) -> ImageResult:
        result = ImageResult()
        t0 = time.monotonic()
        try:
            resp = await self._client.post(self._url, json=body)
            result.http_status = resp.status_code
            result.e2e_s = time.monotonic() - t0
            result.response_bytes = len(resp.content)
            if resp.status_code >= 400:
                result.retry_after_s = _extract_retry_after_seconds(resp)
                result.error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                result.insufficient_quota = _is_insufficient_quota_error(result.error)
                return result
            payload = resp.json()
            data = payload.get("data") or []
            result.images = len(data)
            result.has_b64_json = any(bool(item.get("b64_json")) for item in data if isinstance(item, dict))
            result.has_url = any(bool(item.get("url")) for item in data if isinstance(item, dict))
            result.revised_prompt_count = sum(
                1 for item in data if isinstance(item, dict) and item.get("revised_prompt")
            )
            result.success = True
        except httpx.TimeoutException as e:
            result.e2e_s = time.monotonic() - t0
            result.error = f"timeout: {e}"
        except httpx.HTTPError as e:
            result.e2e_s = time.monotonic() - t0
            result.error = f"http: {e}"
        except Exception as e:  # pragma: no cover - safety net
            result.e2e_s = time.monotonic() - t0
            result.error = f"unexpected: {e!r}"
        result.insufficient_quota = _is_insufficient_quota_error(result.error)
        return result


def _extract_retry_after_seconds(resp: httpx.Response) -> float:
    retry_after = resp.headers.get("retry-after")
    if retry_after:
        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            pass
    match = _RETRY_AFTER_RE.search(resp.text or "")
    if match:
        try:
            return max(float(match.group(1)), 0.0)
        except ValueError:
            return 0.0
    return 0.0


def _is_insufficient_quota_error(message: str) -> bool:
    lowered = message.lower()
    return any(pattern in lowered for pattern in _INSUFFICIENT_QUOTA_PATTERNS)
