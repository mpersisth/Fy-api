"""HTTP client wrapper for fy-api admin and relay APIs."""

import time
from dataclasses import dataclass, field

import httpx


@dataclass
class RequestResult:
    request_id: str
    status_code: int
    body: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0
    error: str = ""


class FyApiClient:
    def __init__(self, base_url: str, root_token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.root_token = root_token
        self.timeout = timeout

    def _headers(self, token: str | None = None) -> dict:
        t = token or self.root_token
        return {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}

    def _admin_headers(self) -> dict:
        return self._headers(self.root_token)

    # --- Admin APIs ---

    def create_channel(self, channel_data: dict) -> dict:
        resp = httpx.post(
            f"{self.base_url}/api/channel/",
            json={"mode": "single", "channel": channel_data},
            headers=self._admin_headers(),
            timeout=self.timeout,
        )
        return resp.json()

    def update_channel(self, channel_data: dict) -> dict:
        resp = httpx.put(
            f"{self.base_url}/api/channel/",
            json=channel_data,
            headers=self._admin_headers(),
            timeout=self.timeout,
        )
        return resp.json()

    def delete_channel(self, channel_id: int) -> dict:
        resp = httpx.delete(
            f"{self.base_url}/api/channel/{channel_id}",
            headers=self._admin_headers(),
            timeout=self.timeout,
        )
        return resp.json()

    def set_channel_status(self, channel_id: int, status: int) -> dict:
        return self.update_channel({"id": channel_id, "status": status})

    def create_token(self, name: str, quota: int = 10_000_000) -> dict:
        resp = httpx.post(
            f"{self.base_url}/api/token/",
            json={"name": name, "remain_quota": quota, "unlimited_quota": False},
            headers=self._admin_headers(),
            timeout=self.timeout,
        )
        return resp.json()

    def get_token_key(self, token_id: int) -> str:
        resp = httpx.post(
            f"{self.base_url}/api/token/{token_id}/key",
            headers=self._admin_headers(),
            timeout=self.timeout,
        )
        data = resp.json()
        return data.get("data", "")

    def delete_token(self, token_id: int) -> dict:
        resp = httpx.delete(
            f"{self.base_url}/api/token/{token_id}",
            headers=self._admin_headers(),
            timeout=self.timeout,
        )
        return resp.json()

    def update_option(self, key: str, value: str) -> dict:
        resp = httpx.put(
            f"{self.base_url}/api/option/",
            json={"key": key, "value": value},
            headers=self._admin_headers(),
            timeout=self.timeout,
        )
        return resp.json()

    def clear_affinity_cache(self) -> dict:
        resp = httpx.delete(
            f"{self.base_url}/api/option/channel_affinity_cache",
            headers=self._admin_headers(),
            timeout=self.timeout,
        )
        return resp.json()

    def get_affinity_cache_stats(self) -> dict:
        resp = httpx.get(
            f"{self.base_url}/api/option/channel_affinity_cache",
            headers=self._admin_headers(),
            timeout=self.timeout,
        )
        return resp.json()

    def search_logs(self, params: dict) -> dict:
        resp = httpx.get(
            f"{self.base_url}/api/log/search",
            params=params,
            headers=self._admin_headers(),
            timeout=self.timeout,
        )
        return resp.json()

    # --- Relay API (send actual model requests) ---

    def chat_completion(
        self, token: str, model: str, messages: list, **kwargs
    ) -> RequestResult:
        start = time.time()
        try:
            resp = httpx.post(
                f"{self.base_url}/v1/chat/completions",
                json={"model": model, "messages": messages, "max_tokens": 5, **kwargs},
                headers=self._headers(token),
                timeout=self.timeout,
            )
            elapsed = (time.time() - start) * 1000
            request_id = resp.headers.get("X-Oneapi-Request-Id", "")
            body = resp.json() if resp.status_code == 200 else {}
            error = "" if resp.status_code == 200 else resp.text[:200]
            return RequestResult(
                request_id=request_id,
                status_code=resp.status_code,
                body=body,
                elapsed_ms=elapsed,
                error=error,
            )
        except Exception as e:
            return RequestResult(
                request_id="",
                status_code=0,
                elapsed_ms=(time.time() - start) * 1000,
                error=str(e),
            )
