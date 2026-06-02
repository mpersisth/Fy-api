"""Test 2-3: Priority fallback and priority+weight combined."""

import time
from dataclasses import dataclass, field

from lib.client import FyApiClient
from lib.harness import ChannelConfig, TestHarness
from lib.log_query import DistributionResult, query_distribution_by_token


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
) -> int:
    errors = 0
    for i in range(count):
        result = client.chat_completion(
            token=token, model=model, messages=[{"role": "user", "content": "say ok"}]
        )
        if result.error:
            errors += 1
        if interval > 0 and i < count - 1:
            time.sleep(interval)
    return errors


def run_priority_fallback_test(
    client: FyApiClient,
    harness: TestHarness,
    num_requests: int = 30,
    interval: float = 0.1,
    log_wait: float = 3.0,
) -> PriorityTestResult:
    """Test priority-based fallback when higher-priority channels are disabled."""
    channels = [
        ChannelConfig(name="prio-high", priority=1000, weight=100),
        ChannelConfig(name="prio-mid", priority=500, weight=100),
        ChannelConfig(name="prio-low", priority=100, weight=100),
    ]
    ctx = harness.setup_channels(channels)
    print(f"  Created channels: {ctx.channel_names}")
    print(f"  Token: {ctx.token_name} (id={ctx.token_id})")
    result = PriorityTestResult(
        name="Priority Fallback (1000 > 500 > 100)", channel_names=ctx.channel_names
    )

    # Scenario A: all healthy — traffic to highest priority
    print(f"  Scenario A: all healthy, sending {num_requests} requests...")
    ts_a = int(time.time())
    _send_requests(client, ctx.token_key, harness.model, num_requests, interval)
    dist_a = query_distribution_by_token(client, ctx.token_name, start_timestamp=ts_a, wait_seconds=log_wait)
    high_id = channels[0].channel_id
    high_count = dist_a.by_channel.get(high_id, 0)
    all_to_high = high_count == dist_a.total and dist_a.total > 0
    result.scenarios.append(PriorityScenarioResult(
        name="All healthy -> highest priority",
        distribution=dist_a,
        expected_channel_names=["prio-high"],
        actual_channel_names=[ctx.channel_names.get(c, f"external-{c}") for c in dist_a.by_channel],
        passed=all_to_high,
        detail=f"{high_count}/{dist_a.total} to prio-high",
    ))

    # Scenario B: disable highest → traffic to mid
    print("  Scenario B: disabling prio-high...")
    harness.disable_channel(channels[0].channel_id)
    time.sleep(1)
    ts_b = int(time.time())
    _send_requests(client, ctx.token_key, harness.model, num_requests, interval)
    dist_b = query_distribution_by_token(client, ctx.token_name, start_timestamp=ts_b, wait_seconds=log_wait)
    mid_id = channels[1].channel_id
    mid_count = dist_b.by_channel.get(mid_id, 0)
    all_to_mid = mid_count == dist_b.total and dist_b.total > 0
    result.scenarios.append(PriorityScenarioResult(
        name="Highest disabled -> mid priority",
        distribution=dist_b,
        expected_channel_names=["prio-mid"],
        actual_channel_names=[ctx.channel_names.get(c, f"external-{c}") for c in dist_b.by_channel],
        passed=all_to_mid,
        detail=f"{mid_count}/{dist_b.total} to prio-mid",
    ))

    # Scenario C: disable mid too → traffic to low
    print("  Scenario C: disabling prio-mid...")
    harness.disable_channel(channels[1].channel_id)
    time.sleep(1)
    ts_c = int(time.time())
    _send_requests(client, ctx.token_key, harness.model, num_requests, interval)
    dist_c = query_distribution_by_token(client, ctx.token_name, start_timestamp=ts_c, wait_seconds=log_wait)
    low_id = channels[2].channel_id
    low_count = dist_c.by_channel.get(low_id, 0)
    all_to_low = low_count == dist_c.total and dist_c.total > 0
    result.scenarios.append(PriorityScenarioResult(
        name="Top two disabled -> lowest priority",
        distribution=dist_c,
        expected_channel_names=["prio-low"],
        actual_channel_names=[ctx.channel_names.get(c, f"external-{c}") for c in dist_c.by_channel],
        passed=all_to_low,
        detail=f"{low_count}/{dist_c.total} to prio-low",
    ))

    harness.teardown(ctx)
    return result
