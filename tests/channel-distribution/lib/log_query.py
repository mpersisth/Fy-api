"""Log query utilities — fetch and aggregate channel distribution from fy-api logs."""

import time
from collections import Counter
from dataclasses import dataclass, field

from .client import FyApiClient


@dataclass
class DistributionResult:
    total: int = 0
    by_channel: Counter = field(default_factory=Counter)
    errors: int = 0
    request_ids: list = field(default_factory=list)

    @property
    def channel_ids(self) -> list[int]:
        return sorted(self.by_channel.keys())

    def pct(self, channel_id: int) -> float:
        if self.total == 0:
            return 0.0
        return self.by_channel[channel_id] / self.total * 100

    def summary(self, channel_names: dict[int, str] | None = None) -> str:
        lines = [f"Total: {self.total} requests ({self.errors} errors)"]
        for ch_id in self.channel_ids:
            count = self.by_channel[ch_id]
            name = (channel_names or {}).get(ch_id, f"channel-{ch_id}")
            lines.append(f"  {name}: {count} ({self.pct(ch_id):.1f}%)")
        return "\n".join(lines)


def query_distribution_by_token(
    client: FyApiClient,
    token_name: str,
    start_timestamp: int = 0,
    wait_seconds: float = 3.0,
) -> DistributionResult:
    """Query logs by token name and aggregate channel distribution."""
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    result = DistributionResult()
    page = 0
    page_size = 100
    while True:
        params = {"token_name": token_name, "p": page, "page_size": page_size}
        if start_timestamp > 0:
            params["start_timestamp"] = start_timestamp
        resp = client.search_logs(params)
        data = resp.get("data", {})
        items = data.get("items", []) if isinstance(data, dict) else []
        if not items:
            break
        for log in items:
            ch_id = log.get("channel", 0)
            rid = log.get("request_id", "")
            if rid:
                result.request_ids.append(rid)
            if ch_id > 0:
                result.by_channel[ch_id] += 1
                result.total += 1
            else:
                result.errors += 1
        total = data.get("total", 0)
        if (page + 1) * page_size >= total:
            break
        page += 1
    return result


def query_distribution(
    client: FyApiClient,
    request_ids: list[str],
    wait_seconds: float = 3.0,
) -> DistributionResult:
    """Query logs by request IDs and aggregate channel distribution."""
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    result = DistributionResult(request_ids=request_ids)
    for rid in request_ids:
        if not rid:
            result.errors += 1
            continue
        resp = client.search_logs({"request_id": rid, "p": 0, "page_size": 1})
        data = resp.get("data", {})
        logs = data.get("items", []) if isinstance(data, dict) else []
        if logs:
            ch_id = logs[0].get("channel", 0)
            if ch_id > 0:
                result.by_channel[ch_id] += 1
                result.total += 1
            else:
                result.errors += 1
        else:
            result.errors += 1
    return result
