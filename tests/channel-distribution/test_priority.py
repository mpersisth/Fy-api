"""Test 2-3: Priority fallback and priority+weight combined."""

import time
from dataclasses import dataclass, field

from .lib.client import FyApiClient
from .lib.harness import ChannelConfig, TestContext, TestHarness
from .lib.log_query import DistributionResult, query_distribution
from .lib.stats import chi_squared_test


@dataclass
class PriorityScenarioResult:
    name: str
    distribution: DistributionResult
    expected_channel_names: list[str]
    actual_channel_names: list[str]
    passed: bool
    detail: str = ""


@dataclass
class PriorityTestResult:
    name: str
    scenarios: list[PriorityScenarioResult] = field(default_factory=list)
    channel_names: dict[int, str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.scenarios)

    def report(self) -> str:
        lines = [f"\n{'='*60}", f"  {self.name}", f"{'='*60}"]
        for s in self.scenarios:
            status = "PASS" if s.passed else "FAIL"
            lines.append(f"\n  [{status}] {s.name}")
            lines.append(f"    Expected traffic to: {s.expected_channel_names}")
            lines.append(f"    Actual traffic to:   {s.actual_channel_names}")
            if s.detail:
                lines.append(f"    {s.detail}")
        return "\n".join(lines)


def _send_requests(
    client: FyApiClient, token: str, model: str, count: int, interval: float
) -> list[str]:
    request_ids = []
    for i in range(count):
        result = client.chat_completion(
            token=token, model=model, messages=[{"role": "user", "content": "say ok"}]
        )
        request_ids.append(result.request_id)
        if interval > 0 and i < count - 1:
            time.sleep(interval)
    return request_ids


def run_priority_fallback_test(
    client: FyApiClient,
    harness: TestHarness,
    num_requests: int = 30,
    interval: float = 0.1,
    log_wait: float = 3.0,
) -> PriorityTestResult:
    """Test priority-based fallback when higher-priority channels are disabled."""
    channels = [
        ChannelConfig(name="prio-high", priority=100, weight=100),
        ChannelConfig(name="prio-mid", priority=50, weight=100),
        ChannelConfig(name="prio-low", priority=10, weight=100),
    ]
    ctx = harness.setup_channels(channels)
    print(f"  Created channels: {ctx.channel_names}")
    result = PriorityTestResult(
        name="Priority Fallback (100 > 50 > 10)", channel_names=ctx.channel_names
    )

    # Scenario A: all healthy — traffic to highest priority
    print(f"  Scenario A: all healthy, sending {num_requests} requests...")
    ids_a = _send_requests(client, ctx.token_key, harness.model, num_requests, interval)
    dist_a = query_distribution(client, ids_a, wait_seconds=log_wait)
    high_id = channels[0].channel_id
    all_to_high = dist_a.by_channel.get(high_id, 0) == dist_a.total
    result.scenarios.append(PriorityScenarioResult(
        name="All healthy → highest priority",
        distribution=dist_a,
        expected_channel_names=["prio-high"],
        actual_channel_names=[ctx.channel_names[c] for c in dist_a.by_channel],
        passed=all_to_high,
        detail=f"{dist_a.by_channel.get(high_id, 0)}/{dist_a.total} to prio-high",
    ))

    # Scenario B: disable highest → traffic to mid
    print("  Scenario B: disabling prio-high...")
    harness.disable_channel(channels[0].channel_id)
    time.sleep(1)
    ids_b = _send_requests(client, ctx.token_key, harness.model, num_requests, interval)
    dist_b = query_distribution(client, ids_b, wait_seconds=log_wait)
    mid_id = channels[1].channel_id
    all_to_mid = dist_b.by_channel.get(mid_id, 0) == dist_b.total
    result.scenarios.append(PriorityScenarioResult(
        name="Highest disabled → mid priority",
        distribution=dist_b,
        expected_channel_names=["prio-mid"],
        actual_channel_names=[ctx.channel_names[c] for c in dist_b.by_channel],
        passed=all_to_mid,
        detail=f"{dist_b.by_channel.get(mid_id, 0)}/{dist_b.total} to prio-mid",
    ))

    # Scenario C: disable mid too → traffic to low
    print("  Scenario C: disabling prio-mid...")
    harness.disable_channel(channels[1].channel_id)
    time.sleep(1)
    ids_c = _send_requests(client, ctx.token_key, harness.model, num_requests, interval)
    dist_c = query_distribution(client, ids_c, wait_seconds=log_wait)
    low_id = channels[2].channel_id
    all_to_low = dist_c.by_channel.get(low_id, 0) == dist_c.total
    result.scenarios.append(PriorityScenarioResult(
        name="Top two disabled → lowest priority",
        distribution=dist_c,
        expected_channel_names=["prio-low"],
        actual_channel_names=[ctx.channel_names[c] for c in dist_c.by_channel],
        passed=all_to_low,
        detail=f"{dist_c.by_channel.get(low_id, 0)}/{dist_c.total} to prio-low",
    ))

    harness.teardown(ctx)
    return result
