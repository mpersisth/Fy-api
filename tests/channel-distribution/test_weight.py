"""Test 1: Weight distribution verification."""

import time
from dataclasses import dataclass

from lib.client import FyApiClient
from lib.harness import ChannelConfig, TestHarness
from lib.log_query import DistributionResult, query_distribution_by_token
from lib.stats import chi_squared_test


@dataclass
class WeightTestResult:
    name: str
    total_requests: int
    distribution: DistributionResult
    expected_weights: dict[int, int]
    chi2: float
    p_value: float
    passed: bool
    channel_names: dict[int, str]

    def report(self) -> str:
        lines = [f"\n{'='*60}", f"  {self.name}", f"{'='*60}"]
        lines.append(f"  Requests: {self.total_requests} | Errors: {self.distribution.errors}")
        lines.append("")
        total_weight = sum(self.expected_weights.values())
        for ch_id in sorted(self.expected_weights.keys()):
            name = self.channel_names.get(ch_id, f"ch-{ch_id}")
            actual_pct = self.distribution.pct(ch_id)
            expected_pct = self.expected_weights[ch_id] / total_weight * 100
            count = self.distribution.by_channel.get(ch_id, 0)
            lines.append(
                f"  {name:20s}: {count:4d} ({actual_pct:5.1f}%) — expected {expected_pct:.1f}%"
            )
        lines.append("")
        status = "PASS" if self.passed else "FAIL"
        lines.append(f"  Chi-squared: {self.chi2:.2f}, p-value: {self.p_value:.4f} — {status}")
        return "\n".join(lines)


def run_weight_test(
    client: FyApiClient,
    harness: TestHarness,
    num_requests: int = 300,
    interval: float = 0.1,
    log_wait: float = 3.0,
) -> WeightTestResult:
    """Test weight distribution with 3 channels at same priority."""
    channels = [
        ChannelConfig(name="weight-A", priority=1000, weight=50),
        ChannelConfig(name="weight-B", priority=1000, weight=30),
        ChannelConfig(name="weight-C", priority=1000, weight=20),
    ]

    ctx = harness.setup_channels(channels)
    print(f"  Created channels: {ctx.channel_names}")
    print(f"  Token: {ctx.token_name} (id={ctx.token_id})")
    print(f"  Sending {num_requests} requests...")

    start_ts = int(time.time())
    errors = 0
    for i in range(num_requests):
        result = client.chat_completion(
            token=ctx.token_key,
            model=harness.model,
            messages=[{"role": "user", "content": "say ok"}],
        )
        if result.error:
            errors += 1
            if errors <= 3:
                print(f"  [WARN] request {i}: {result.error[:80]}")
        if interval > 0 and i < num_requests - 1:
            time.sleep(interval)
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{num_requests} sent")

    dist = query_distribution_by_token(
        client, ctx.token_name, start_timestamp=start_ts, wait_seconds=log_wait
    )
    expected = {ch.channel_id: ch.weight for ch in channels}
    chi2, p_value, passed = chi_squared_test(dist.by_channel, expected)

    harness.teardown(ctx)

    return WeightTestResult(
        name="Weight Distribution (50/30/20, same priority)",
        total_requests=num_requests,
        distribution=dist,
        expected_weights=expected,
        chi2=chi2,
        p_value=p_value,
        passed=passed,
        channel_names=ctx.channel_names,
    )
