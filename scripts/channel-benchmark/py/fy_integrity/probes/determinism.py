"""P1: Determinism probe.

Sends the same prompt N times at temperature=0 and checks response
consistency. Low consistency suggests multiple upstreams or intermediary
processing.
"""

from __future__ import annotations

from .base import BaseProbe, ProbeResult


class DeterminismProbe(BaseProbe):
    name = "determinism"
    severity = "warning"

    async def run(self, client, config) -> ProbeResult:
        rounds = config.probes.determinism.rounds
        min_consistency = config.probes.determinism.min_consistency
        model = config.target.model

        prompt = "What is 2+2? Reply with only the number and nothing else."
        messages = [{"role": "user", "content": prompt}]

        responses: list[str] = []
        evidence: list[dict] = []

        for i in range(rounds):
            result = await client.complete(
                model=model,
                messages=messages,
                max_tokens=32,
                temperature=0.0,
            )
            if not result.success:
                evidence.append({"round": i, "error": result.error})
                continue
            content = result.content.strip()
            responses.append(content)
            evidence.append({"round": i, "response": content})

        if len(responses) < 2:
            return self.skip_result("fewer than 2 successful responses")

        consistency = _pairwise_consistency(responses)
        unique = list(set(responses))

        details = {
            "consistency_rate": consistency,
            "min_consistency": min_consistency,
            "unique_responses": unique,
            "total_responses": len(responses),
        }

        if consistency < min_consistency:
            return self.fail_result(
                f"consistency={consistency:.0%} < threshold={min_consistency:.0%} "
                f"({len(unique)} unique responses in {len(responses)} rounds)",
                evidence=evidence,
                **details,
            )

        return self.pass_result(
            f"consistency={consistency:.0%} (threshold={min_consistency:.0%})",
            **details,
        )


def _pairwise_consistency(responses: list[str]) -> float:
    """Fraction of pairs that are identical."""
    n = len(responses)
    if n < 2:
        return 1.0
    matches = sum(
        1
        for i in range(n)
        for j in range(i + 1, n)
        if responses[i] == responses[j]
    )
    total_pairs = n * (n - 1) // 2
    return matches / total_pairs
