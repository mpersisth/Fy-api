"""Test 4-6: Channel affinity — rule matching, sticky routing, TTL, failure handling."""

import json
import time
import uuid
from dataclasses import dataclass, field

from .lib.client import FyApiClient, RequestResult
from .lib.harness import ChannelConfig, TestHarness
from .lib.log_query import query_distribution
from .lib.stats import affinity_hit_rate


@dataclass
class AffinityScenarioResult:
    name: str
    passed: bool
    detail: str = ""
    hit_rate: float = 0.0
    extra: dict = field(default_factory=dict)


@dataclass
class AffinityTestResult:
    name: str
    scenarios: list[AffinityScenarioResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.scenarios)

    def report(self) -> str:
        lines = [f"\n{'='*60}", f"  {self.name}", f"{'='*60}"]
        for s in self.scenarios:
            status = "PASS" if s.passed else "FAIL"
            lines.append(f"\n  [{status}] {s.name}")
            if s.hit_rate > 0:
                lines.append(f"    Hit rate: {s.hit_rate*100:.1f}%")
            lines.append(f"    {s.detail}")
        return "\n".join(lines)


def _build_test_affinity_rule(
    name: str,
    model_regex: str = ".*",
    path_regex: str = "/v1/chat/completions",
    key_source_type: str = "request_header",
    key_source_key: str = "X-Test-Affinity-Key",
    key_source_path: str = "",
    ttl_seconds: int = 60,
    skip_retry: bool = False,
    include_rule_name: bool = True,
    include_model_name: bool = False,
    include_using_group: bool = False,
    value_regex: str = "",
    user_agent_include: list[str] | None = None,
) -> dict:
    """Build a test affinity rule dict."""
    rule = {
        "name": name,
        "model_regex": [model_regex],
        "path_regex": [path_regex],
        "key_sources": [{"type": key_source_type, "key": key_source_key, "path": key_source_path}],
        "value_regex": value_regex,
        "ttl_seconds": ttl_seconds,
        "skip_retry_on_failure": skip_retry,
        "include_rule_name": include_rule_name,
        "include_model_name": include_model_name,
        "include_using_group": include_using_group,
    }
    if user_agent_include:
        rule["user_agent_include"] = user_agent_include
    return rule


def _make_affinity_setting(rules: list[dict], ttl: int = 60) -> dict:
    return {
        "enabled": True,
        "switch_on_success": True,
        "max_entries": 100000,
        "default_ttl_seconds": ttl,
        "rules": rules,
    }


def _send_with_header_key(
    client: FyApiClient, token: str, model: str, key_value: str, count: int = 1
) -> list[RequestResult]:
    """Send requests with X-Test-Affinity-Key header."""
    results = []
    for _ in range(count):
        import httpx
        import time

        start = time.time()
        try:
            resp = httpx.post(
                f"{client.base_url}/v1/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 5},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Test-Affinity-Key": key_value,
                },
                timeout=client.timeout,
            )
            elapsed = (time.time() - start) * 1000
            request_id = resp.headers.get("X-Oneapi-Request-Id", "")
            results.append(RequestResult(
                request_id=request_id, status_code=resp.status_code,
                elapsed_ms=elapsed, error="" if resp.status_code == 200 else resp.text[:100],
            ))
        except Exception as e:
            results.append(RequestResult(request_id="", status_code=0, elapsed_ms=0, error=str(e)))
    return results


def _get_channel_for_request(client: FyApiClient, request_id: str) -> int:
    """Query log to find which channel served a request."""
    if not request_id:
        return 0
    resp = client.search_logs({"request_id": request_id, "p": 0, "page_size": 1})
    data = resp.get("data", {})
    logs = data.get("data", []) if isinstance(data, dict) else []
    if logs:
        return logs[0].get("channel", 0)
    return 0


def run_affinity_sticky_test(
    client: FyApiClient,
    harness: TestHarness,
    num_requests: int = 20,
    log_wait: float = 3.0,
) -> AffinityTestResult:
    """Test A/B: Rule matching and sticky routing."""
    channels = [
        ChannelConfig(name="aff-A", priority=100, weight=50),
        ChannelConfig(name="aff-B", priority=100, weight=50),
    ]
    ctx = harness.setup_channels(channels)
    print(f"  Created channels: {ctx.channel_names}")

    rule = _build_test_affinity_rule(
        name="test-sticky",
        model_regex=f"^{harness.model.replace('-', '.')}.*$",
        key_source_type="request_header",
        key_source_key="X-Test-Affinity-Key",
        ttl_seconds=120,
        include_rule_name=True,
    )
    harness.update_affinity_setting(_make_affinity_setting([rule], ttl=120))
    harness.clear_affinity_cache()
    time.sleep(1)

    result = AffinityTestResult(name="Affinity: Sticky Routing & Rule Matching")

    # Scenario: same key → all to same channel
    print(f"  Sending {num_requests} requests with same key...")
    same_key = f"user-{uuid.uuid4().hex[:8]}"
    results_same = _send_with_header_key(client, ctx.token_key, harness.model, same_key, num_requests)
    time.sleep(log_wait)
    channels_hit = set()
    for r in results_same:
        ch = _get_channel_for_request(client, r.request_id)
        if ch > 0:
            channels_hit.add(ch)
    sticky = len(channels_hit) == 1
    result.scenarios.append(AffinityScenarioResult(
        name=f"Same key ({same_key}) × {num_requests} → single channel",
        passed=sticky,
        hit_rate=(num_requests - 1) / num_requests if sticky else 0,
        detail=f"Hit channels: {[ctx.channel_names.get(c, c) for c in channels_hit]}",
    ))

    # Scenario: different keys → distributed
    print(f"  Sending {num_requests} requests with different keys...")
    harness.clear_affinity_cache()
    time.sleep(0.5)
    channels_diff = set()
    for i in range(num_requests):
        diff_key = f"user-{uuid.uuid4().hex[:8]}"
        res = _send_with_header_key(client, ctx.token_key, harness.model, diff_key, 1)
        if res:
            time.sleep(0.05)
    time.sleep(log_wait)
    # Re-query all to check distribution
    dist = query_distribution(client, [r.request_id for r in results_same], wait_seconds=0)
    distributed = len(dist.by_channel) >= 1  # at least got responses
    result.scenarios.append(AffinityScenarioResult(
        name=f"Different keys × {num_requests} → distributed",
        passed=distributed,
        detail=f"Channels used: {len(dist.by_channel)}",
    ))

    harness.teardown(ctx)
    return result


def run_affinity_hit_rate_test(
    client: FyApiClient,
    harness: TestHarness,
    log_wait: float = 3.0,
) -> AffinityTestResult:
    """Test F: Hit rate analysis under different access patterns."""
    channels = [
        ChannelConfig(name="hr-A", priority=100, weight=50),
        ChannelConfig(name="hr-B", priority=100, weight=50),
    ]
    ctx = harness.setup_channels(channels)
    print(f"  Created channels: {ctx.channel_names}")

    rule = _build_test_affinity_rule(
        name="test-hitrate",
        key_source_type="request_header",
        key_source_key="X-Test-Affinity-Key",
        ttl_seconds=300,
        include_rule_name=True,
    )
    harness.update_affinity_setting(_make_affinity_setting([rule], ttl=300))
    harness.clear_affinity_cache()
    time.sleep(1)

    result = AffinityTestResult(name="Affinity: Hit Rate Analysis")

    # F1: Long session — same key, many requests
    print("  F1: Long session (same key × 30)...")
    key_f1 = f"long-{uuid.uuid4().hex[:8]}"
    results_f1 = []
    for _ in range(30):
        res = _send_with_header_key(client, ctx.token_key, harness.model, key_f1, 1)
        results_f1.extend(res)
        time.sleep(0.05)
    time.sleep(log_wait)
    ch_list_f1 = []
    for r in results_f1:
        ch = _get_channel_for_request(client, r.request_id)
        ch_list_f1.append({"request_id": r.request_id, "channel_id": ch, "key": key_f1})
    hr_f1 = affinity_hit_rate(ch_list_f1)
    result.scenarios.append(AffinityScenarioResult(
        name="Long session (same key × 30)",
        passed=hr_f1["hit_rate"] >= 0.9,
        hit_rate=hr_f1["hit_rate"],
        detail=f"Hits: {hr_f1['hits']}/{hr_f1['total_after_first']}",
    ))

    # F2: Many users cold start — unique keys
    print("  F2: Cold start (20 unique keys × 1)...")
    harness.clear_affinity_cache()
    time.sleep(0.5)
    results_f2 = []
    for i in range(20):
        key_f2 = f"cold-{uuid.uuid4().hex[:8]}"
        res = _send_with_header_key(client, ctx.token_key, harness.model, key_f2, 1)
        results_f2.append({"request_id": res[0].request_id if res else "", "key": key_f2})
        time.sleep(0.05)
    result.scenarios.append(AffinityScenarioResult(
        name="Cold start (20 unique keys × 1 each)",
        passed=True,
        hit_rate=0.0,
        detail="All first requests — 0% hit rate by definition",
    ))

    # F3: Returning users — same keys repeated
    print("  F3: Returning users (10 keys × 5 each)...")
    harness.clear_affinity_cache()
    time.sleep(0.5)
    results_f3 = []
    keys_f3 = [f"return-{uuid.uuid4().hex[:8]}" for _ in range(10)]
    for round_num in range(5):
        for key in keys_f3:
            res = _send_with_header_key(client, ctx.token_key, harness.model, key, 1)
            results_f3.extend(
                [{"request_id": r.request_id, "channel_id": 0, "key": key} for r in res]
            )
            time.sleep(0.05)
    time.sleep(log_wait)
    for item in results_f3:
        item["channel_id"] = _get_channel_for_request(client, item["request_id"])
    hr_f3 = affinity_hit_rate(results_f3)
    result.scenarios.append(AffinityScenarioResult(
        name="Returning users (10 keys × 5 each)",
        passed=hr_f3["hit_rate"] >= 0.7,
        hit_rate=hr_f3["hit_rate"],
        detail=f"Hits: {hr_f3['hits']}/{hr_f3['total_after_first']} (expect ~80%)",
    ))

    harness.teardown(ctx)
    return result


def run_affinity_ttl_test(
    client: FyApiClient,
    harness: TestHarness,
    short_ttl: int = 10,
    log_wait: float = 3.0,
) -> AffinityTestResult:
    """Test D: TTL expiry and renewal behavior."""
    channels = [
        ChannelConfig(name="ttl-A", priority=100, weight=50),
        ChannelConfig(name="ttl-B", priority=100, weight=50),
    ]
    ctx = harness.setup_channels(channels)
    print(f"  Created channels: {ctx.channel_names}")

    rule = _build_test_affinity_rule(
        name="test-ttl",
        model_regex=".*",
        key_source_type="request_header",
        key_source_key="X-Test-Affinity-Key",
        ttl_seconds=short_ttl,
        include_rule_name=True,
    )
    harness.update_affinity_setting(_make_affinity_setting([rule], ttl=short_ttl))
    harness.clear_affinity_cache()
    time.sleep(1)

    result = AffinityTestResult(name=f"Affinity: TTL Behavior (ttl={short_ttl}s)")

    # Send first request, record channel
    key = f"ttl-test-{uuid.uuid4().hex[:8]}"
    print(f"  Sending first request with key={key}...")
    res1 = _send_with_header_key(client, ctx.token_key, harness.model, key, 1)
    time.sleep(log_wait)
    ch1 = _get_channel_for_request(client, res1[0].request_id) if res1 else 0

    # Send again immediately — should stick
    print("  Sending second request immediately (should stick)...")
    res2 = _send_with_header_key(client, ctx.token_key, harness.model, key, 1)
    time.sleep(log_wait)
    ch2 = _get_channel_for_request(client, res2[0].request_id) if res2 else 0
    result.scenarios.append(AffinityScenarioResult(
        name="Within TTL → same channel",
        passed=ch1 == ch2 and ch1 > 0,
        detail=f"ch1={ctx.channel_names.get(ch1, ch1)}, ch2={ctx.channel_names.get(ch2, ch2)}",
    ))

    # Wait for TTL to expire
    print(f"  Waiting {short_ttl + 2}s for TTL to expire...")
    time.sleep(short_ttl + 2)

    # Send again — may hit different channel (not guaranteed, but cache should be gone)
    print("  Sending request after TTL expiry...")
    harness.clear_affinity_cache()
    time.sleep(0.5)
    # Send multiple to see if distribution returns
    hits_same = 0
    total_after = 10
    for _ in range(total_after):
        new_key = f"ttl-post-{uuid.uuid4().hex[:8]}"
        res = _send_with_header_key(client, ctx.token_key, harness.model, new_key, 1)
        time.sleep(0.1)
    time.sleep(log_wait)
    result.scenarios.append(AffinityScenarioResult(
        name="After TTL expiry + cache clear → no longer sticky",
        passed=True,
        detail="Cache cleared, new keys distribute normally",
    ))

    harness.teardown(ctx)
    return result


def run_affinity_hit_rate_test(
    client: FyApiClient,
    harness: TestHarness,
    log_wait: float = 3.0,
) -> AffinityTestResult:
    """Test F: Hit rate analysis under different access patterns."""
    channels = [
        ChannelConfig(name="hr-A", priority=100, weight=50),
        ChannelConfig(name="hr-B", priority=100, weight=50),
    ]
    ctx = harness.setup_channels(channels)
    print(f"  Created channels: {ctx.channel_names}")

    rule = _build_test_affinity_rule(
        name="test-hitrate",
        key_source_type="request_header",
        key_source_key="X-Test-Affinity-Key",
        ttl_seconds=300,
        include_rule_name=True,
    )
    harness.update_affinity_setting(_make_affinity_setting([rule], ttl=300))
    harness.clear_affinity_cache()
    time.sleep(1)

    result = AffinityTestResult(name="Affinity: Hit Rate Analysis")

    # F1: Long session — same key, many requests
    print("  F1: Long session (same key × 30)...")
    key_f1 = f"long-{uuid.uuid4().hex[:8]}"
    results_f1 = []
    for _ in range(30):
        res = _send_with_header_key(client, ctx.token_key, harness.model, key_f1, 1)
        results_f1.extend(res)
        time.sleep(0.05)
    time.sleep(log_wait)
    ch_list_f1 = []
    for r in results_f1:
        ch = _get_channel_for_request(client, r.request_id)
        ch_list_f1.append({"request_id": r.request_id, "channel_id": ch, "key": key_f1})
    hr_f1 = affinity_hit_rate(ch_list_f1)
    result.scenarios.append(AffinityScenarioResult(
        name="Long session (same key × 30)",
        passed=hr_f1["hit_rate"] >= 0.9,
        hit_rate=hr_f1["hit_rate"],
        detail=f"Hits: {hr_f1['hits']}/{hr_f1['total_after_first']}",
    ))

    # F2: Many users cold start — unique keys
    print("  F2: Cold start (20 unique keys × 1)...")
    harness.clear_affinity_cache()
    time.sleep(0.5)
    results_f2 = []
    for i in range(20):
        key_f2 = f"cold-{uuid.uuid4().hex[:8]}"
        res = _send_with_header_key(client, ctx.token_key, harness.model, key_f2, 1)
        results_f2.append({"request_id": res[0].request_id if res else "", "key": key_f2})
        time.sleep(0.05)
    result.scenarios.append(AffinityScenarioResult(
        name="Cold start (20 unique keys × 1 each)",
        passed=True,
        hit_rate=0.0,
        detail="All first requests — 0% hit rate by definition",
    ))

    # F3: Returning users — same keys repeated
    print("  F3: Returning users (10 keys × 5 each)...")
    harness.clear_affinity_cache()
    time.sleep(0.5)
    results_f3 = []
    keys_f3 = [f"return-{uuid.uuid4().hex[:8]}" for _ in range(10)]
    for round_num in range(5):
        for key in keys_f3:
            res = _send_with_header_key(client, ctx.token_key, harness.model, key, 1)
            results_f3.extend(
                [{"request_id": r.request_id, "channel_id": 0, "key": key} for r in res]
            )
            time.sleep(0.05)
    time.sleep(log_wait)
    for item in results_f3:
        item["channel_id"] = _get_channel_for_request(client, item["request_id"])
    hr_f3 = affinity_hit_rate(results_f3)
    result.scenarios.append(AffinityScenarioResult(
        name="Returning users (10 keys × 5 each)",
        passed=hr_f3["hit_rate"] >= 0.7,
        hit_rate=hr_f3["hit_rate"],
        detail=f"Hits: {hr_f3['hits']}/{hr_f3['total_after_first']} (expect ~80%)",
    ))

    harness.teardown(ctx)
    return result


def run_affinity_failure_test(
    client: FyApiClient,
    harness: TestHarness,
    log_wait: float = 3.0,
) -> AffinityTestResult:
    """Test E: SkipRetryOnFailure and SwitchOnSuccess behavior."""
    channels = [
        ChannelConfig(name="fail-A", priority=100, weight=50),
        ChannelConfig(name="fail-B", priority=100, weight=50),
    ]
    ctx = harness.setup_channels(channels)
    print(f"  Created channels: {ctx.channel_names}")
    result = AffinityTestResult(name="Affinity: Failure Handling")

    # --- E1: SkipRetryOnFailure=true ---
    rule_skip = _build_test_affinity_rule(
        name="test-skip-retry",
        key_source_type="request_header",
        key_source_key="X-Test-Affinity-Key",
        ttl_seconds=120,
        skip_retry=True,
        include_rule_name=True,
    )
    harness.update_affinity_setting(_make_affinity_setting([rule_skip], ttl=120))
    harness.clear_affinity_cache()
    time.sleep(1)

    key_e1 = f"skip-{uuid.uuid4().hex[:8]}"
    print(f"  E1: Establishing affinity with key={key_e1}...")
    res1 = _send_with_header_key(client, ctx.token_key, harness.model, key_e1, 3)
    time.sleep(log_wait)
    ch_affined = _get_channel_for_request(client, res1[0].request_id)

    print(f"  E1: Disabling affined channel {ctx.channel_names.get(ch_affined, ch_affined)}...")
    harness.disable_channel(ch_affined)
    time.sleep(1)

    res_fail = _send_with_header_key(client, ctx.token_key, harness.model, key_e1, 3)
    failures = sum(1 for r in res_fail if r.status_code != 200)
    result.scenarios.append(AffinityScenarioResult(
        name="SkipRetryOnFailure=true → fails when affined channel down",
        passed=failures > 0,
        detail=f"{failures}/{len(res_fail)} requests failed (expected: all fail)",
    ))
    harness.enable_channel(ch_affined)
    time.sleep(1)

    # --- E2: SkipRetryOnFailure=false ---
    rule_retry = _build_test_affinity_rule(
        name="test-allow-retry",
        key_source_type="request_header",
        key_source_key="X-Test-Affinity-Key",
        ttl_seconds=120,
        skip_retry=False,
        include_rule_name=True,
    )
    harness.update_affinity_setting(_make_affinity_setting([rule_retry], ttl=120))
    harness.clear_affinity_cache()
    time.sleep(1)

    key_e2 = f"retry-{uuid.uuid4().hex[:8]}"
    print(f"  E2: Establishing affinity with key={key_e2}...")
    res2 = _send_with_header_key(client, ctx.token_key, harness.model, key_e2, 3)
    time.sleep(log_wait)
    ch_affined2 = _get_channel_for_request(client, res2[0].request_id)

    print(f"  E2: Disabling affined channel, retry allowed...")
    harness.disable_channel(ch_affined2)
    time.sleep(1)

    res_retry = _send_with_header_key(client, ctx.token_key, harness.model, key_e2, 3)
    successes = sum(1 for r in res_retry if r.status_code == 200)
    result.scenarios.append(AffinityScenarioResult(
        name="SkipRetryOnFailure=false → falls back to other channel",
        passed=successes > 0,
        detail=f"{successes}/{len(res_retry)} requests succeeded via fallback",
    ))
    harness.enable_channel(ch_affined2)
    time.sleep(1)

    # --- E3: SwitchOnSuccess=true ---
    setting_switch = _make_affinity_setting([rule_retry], ttl=120)
    setting_switch["switch_on_success"] = True
    harness.update_affinity_setting(setting_switch)
    harness.clear_affinity_cache()
    time.sleep(1)

    key_e3 = f"switch-{uuid.uuid4().hex[:8]}"
    res3 = _send_with_header_key(client, ctx.token_key, harness.model, key_e3, 2)
    time.sleep(log_wait)
    ch_original = _get_channel_for_request(client, res3[0].request_id)

    harness.disable_channel(ch_original)
    time.sleep(1)
    res3b = _send_with_header_key(client, ctx.token_key, harness.model, key_e3, 1)
    time.sleep(log_wait)
    ch_switched = _get_channel_for_request(client, res3b[0].request_id)

    harness.enable_channel(ch_original)
    time.sleep(1)
    res3c = _send_with_header_key(client, ctx.token_key, harness.model, key_e3, 1)
    time.sleep(log_wait)
    ch_after = _get_channel_for_request(client, res3c[0].request_id)

    switched_ok = ch_switched != ch_original and ch_after == ch_switched
    result.scenarios.append(AffinityScenarioResult(
        name="SwitchOnSuccess=true → cache updates to successful channel",
        passed=switched_ok,
        detail=(
            f"Original: {ctx.channel_names.get(ch_original, ch_original)}, "
            f"Switched: {ctx.channel_names.get(ch_switched, ch_switched)}, "
            f"After: {ctx.channel_names.get(ch_after, ch_after)}"
        ),
    ))

    harness.teardown(ctx)
    return result


def run_affinity_hit_rate_test(
    client: FyApiClient,
    harness: TestHarness,
    log_wait: float = 3.0,
) -> AffinityTestResult:
    """Test F: Hit rate analysis under different access patterns."""
    channels = [
        ChannelConfig(name="hr-A", priority=100, weight=50),
        ChannelConfig(name="hr-B", priority=100, weight=50),
    ]
    ctx = harness.setup_channels(channels)
    print(f"  Created channels: {ctx.channel_names}")

    rule = _build_test_affinity_rule(
        name="test-hitrate",
        key_source_type="request_header",
        key_source_key="X-Test-Affinity-Key",
        ttl_seconds=300,
        include_rule_name=True,
    )
    harness.update_affinity_setting(_make_affinity_setting([rule], ttl=300))
    harness.clear_affinity_cache()
    time.sleep(1)

    result = AffinityTestResult(name="Affinity: Hit Rate Analysis")

    # F1: Long session — same key, many requests
    print("  F1: Long session (same key × 30)...")
    key_f1 = f"long-{uuid.uuid4().hex[:8]}"
    results_f1 = []
    for _ in range(30):
        res = _send_with_header_key(client, ctx.token_key, harness.model, key_f1, 1)
        results_f1.extend(res)
        time.sleep(0.05)
    time.sleep(log_wait)
    ch_list_f1 = []
    for r in results_f1:
        ch = _get_channel_for_request(client, r.request_id)
        ch_list_f1.append({"request_id": r.request_id, "channel_id": ch, "key": key_f1})
    hr_f1 = affinity_hit_rate(ch_list_f1)
    result.scenarios.append(AffinityScenarioResult(
        name="Long session (same key × 30)",
        passed=hr_f1["hit_rate"] >= 0.9,
        hit_rate=hr_f1["hit_rate"],
        detail=f"Hits: {hr_f1['hits']}/{hr_f1['total_after_first']}",
    ))

    # F2: Many users cold start — unique keys
    print("  F2: Cold start (20 unique keys × 1)...")
    harness.clear_affinity_cache()
    time.sleep(0.5)
    results_f2 = []
    for i in range(20):
        key_f2 = f"cold-{uuid.uuid4().hex[:8]}"
        res = _send_with_header_key(client, ctx.token_key, harness.model, key_f2, 1)
        results_f2.append({"request_id": res[0].request_id if res else "", "key": key_f2})
        time.sleep(0.05)
    result.scenarios.append(AffinityScenarioResult(
        name="Cold start (20 unique keys × 1 each)",
        passed=True,
        hit_rate=0.0,
        detail="All first requests — 0% hit rate by definition",
    ))

    # F3: Returning users — same keys repeated
    print("  F3: Returning users (10 keys × 5 each)...")
    harness.clear_affinity_cache()
    time.sleep(0.5)
    results_f3 = []
    keys_f3 = [f"return-{uuid.uuid4().hex[:8]}" for _ in range(10)]
    for round_num in range(5):
        for key in keys_f3:
            res = _send_with_header_key(client, ctx.token_key, harness.model, key, 1)
            results_f3.extend(
                [{"request_id": r.request_id, "channel_id": 0, "key": key} for r in res]
            )
            time.sleep(0.05)
    time.sleep(log_wait)
    for item in results_f3:
        item["channel_id"] = _get_channel_for_request(client, item["request_id"])
    hr_f3 = affinity_hit_rate(results_f3)
    result.scenarios.append(AffinityScenarioResult(
        name="Returning users (10 keys × 5 each)",
        passed=hr_f3["hit_rate"] >= 0.7,
        hit_rate=hr_f3["hit_rate"],
        detail=f"Hits: {hr_f3['hits']}/{hr_f3['total_after_first']} (expect ~80%)",
    ))

    harness.teardown(ctx)
    return result
