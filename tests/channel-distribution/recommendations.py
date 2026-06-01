"""Recommendations engine — generates actionable advice from test results."""

from dataclasses import dataclass, field


@dataclass
class Recommendation:
    category: str  # "weight", "priority", "affinity"
    severity: str  # "info", "warning", "action"
    message: str


def generate_recommendations(
    weight_result=None,
    priority_result=None,
    affinity_sticky_result=None,
    affinity_ttl_result=None,
    affinity_failure_result=None,
    affinity_hitrate_result=None,
) -> list[Recommendation]:
    """Analyze all test results and generate recommendations."""
    recs: list[Recommendation] = []

    # Weight recommendations
    if weight_result:
        if weight_result.passed:
            recs.append(Recommendation(
                "weight", "info",
                f"Weight distribution converged within {weight_result.total_requests} requests "
                f"(p={weight_result.p_value:.3f}). Configuration is working correctly.",
            ))
        else:
            recs.append(Recommendation(
                "weight", "warning",
                f"Weight distribution did NOT converge (p={weight_result.p_value:.3f}). "
                f"Consider increasing request count or checking for channel issues.",
            ))

    # Priority recommendations
    if priority_result:
        if priority_result.passed:
            recs.append(Recommendation(
                "priority", "info",
                "Priority fallback working correctly. Traffic routes to highest "
                "available priority tier and falls back on failure.",
            ))
        else:
            failed = [s.name for s in priority_result.scenarios if not s.passed]
            recs.append(Recommendation(
                "priority", "warning",
                f"Priority fallback issues detected in: {failed}. "
                "Check channel cache refresh timing.",
            ))

    # Affinity recommendations
    if affinity_hitrate_result:
        for s in affinity_hitrate_result.scenarios:
            if "Long session" in s.name:
                if s.hit_rate >= 0.95:
                    recs.append(Recommendation(
                        "affinity", "info",
                        f"Long session hit rate {s.hit_rate*100:.0f}% — excellent. "
                        "Affinity is providing strong session consistency.",
                    ))
                elif s.hit_rate >= 0.8:
                    recs.append(Recommendation(
                        "affinity", "info",
                        f"Long session hit rate {s.hit_rate*100:.0f}% — good. "
                        "Minor misses likely from cache warm-up.",
                    ))
                else:
                    recs.append(Recommendation(
                        "affinity", "warning",
                        f"Long session hit rate only {s.hit_rate*100:.0f}%. "
                        "Check TTL settings or cache capacity.",
                    ))
            elif "Returning" in s.name:
                if s.hit_rate >= 0.7:
                    recs.append(Recommendation(
                        "affinity", "info",
                        f"Returning users hit rate {s.hit_rate*100:.0f}% — "
                        "affinity is effective for repeat visitors.",
                    ))
                else:
                    recs.append(Recommendation(
                        "affinity", "warning",
                        f"Returning users hit rate {s.hit_rate*100:.0f}% — "
                        "consider increasing TTL or checking eviction pressure.",
                    ))

    if affinity_failure_result:
        for s in affinity_failure_result.scenarios:
            if "SkipRetry" in s.name and "true" in s.name:
                if s.passed:
                    recs.append(Recommendation(
                        "affinity", "action",
                        "SkipRetryOnFailure=true confirmed: requests FAIL when affined "
                        "channel is down. Use this for session-critical flows (Codex/Claude CLI) "
                        "where consistency > availability. Pair with channel health alerting.",
                    ))
            elif "SkipRetry" in s.name and "false" in s.name:
                if s.passed:
                    recs.append(Recommendation(
                        "affinity", "info",
                        "SkipRetryOnFailure=false confirmed: graceful fallback on failure. "
                        "Use this for general user traffic where availability > consistency.",
                    ))
            elif "SwitchOnSuccess" in s.name:
                if s.passed:
                    recs.append(Recommendation(
                        "affinity", "info",
                        "SwitchOnSuccess=true confirmed: cache self-heals after failover. "
                        "Recommended for most scenarios to avoid sticky-to-dead-channel.",
                    ))

    return recs


def format_recommendations(recs: list[Recommendation]) -> str:
    """Format recommendations as a readable report."""
    if not recs:
        return "\n  No recommendations generated.\n"

    lines = [f"\n{'='*60}", "  RECOMMENDATIONS", f"{'='*60}"]

    severity_icon = {"info": "  ", "warning": "  ", "action": "  "}
    by_category: dict[str, list[Recommendation]] = {}
    for r in recs:
        by_category.setdefault(r.category, []).append(r)

    category_titles = {
        "weight": "Weight / Priority",
        "priority": "Priority Fallback",
        "affinity": "Channel Affinity",
    }

    for cat in ["weight", "priority", "affinity"]:
        cat_recs = by_category.get(cat, [])
        if not cat_recs:
            continue
        lines.append(f"\n  [{category_titles.get(cat, cat)}]")
        for r in cat_recs:
            icon = severity_icon.get(r.severity, "  ")
            lines.append(f"  {icon} {r.message}")

    return "\n".join(lines)
