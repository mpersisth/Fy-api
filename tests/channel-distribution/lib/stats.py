"""Statistical analysis utilities for distribution testing."""

from scipy import stats


def chi_squared_test(
    observed: dict[int, int], expected_weights: dict[int, int]
) -> tuple[float, float, bool]:
    """Run chi-squared goodness-of-fit test.

    Returns (chi2_statistic, p_value, passed).
    passed = True if p > 0.05 (distribution matches expected weights).
    """
    total_observed = sum(observed.values())
    total_weight = sum(expected_weights.values())
    if total_observed == 0 or total_weight == 0:
        return 0.0, 0.0, False

    channel_ids = sorted(set(observed.keys()) | set(expected_weights.keys()))
    obs = []
    exp = []
    for ch_id in channel_ids:
        obs.append(observed.get(ch_id, 0))
        expected_count = (expected_weights.get(ch_id, 0) / total_weight) * total_observed
        exp.append(expected_count)

    chi2, p_value = stats.chisquare(obs, f_exp=exp)
    return float(chi2), float(p_value), p_value > 0.05


def affinity_hit_rate(results: list[dict]) -> dict:
    """Calculate affinity cache hit rate from a sequence of requests.

    Each result dict should have: {"request_id": str, "channel_id": int, "key": str}
    """
    by_key: dict[str, list[int]] = {}
    for r in results:
        key = r.get("key", "")
        ch = r.get("channel_id", 0)
        if key and ch > 0:
            by_key.setdefault(key, []).append(ch)

    total = 0
    hits = 0
    for key, channels in by_key.items():
        if len(channels) < 2:
            total += len(channels)
            continue
        first_channel = channels[0]
        for ch in channels[1:]:
            total += 1
            if ch == first_channel:
                hits += 1

    return {
        "total_after_first": total,
        "hits": hits,
        "hit_rate": hits / total if total > 0 else 0.0,
        "unique_keys": len(by_key),
    }
