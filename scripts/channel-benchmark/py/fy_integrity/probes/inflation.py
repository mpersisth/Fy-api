"""P1: Token inflation probe.

Compares locally-counted tokens (via tiktoken) against the API-reported
prompt_tokens to detect hidden content injection.
"""

from __future__ import annotations

from .base import BaseProbe, ProbeResult


class TokenInflationProbe(BaseProbe):
    name = "token_inflation"
    severity = "critical"

    async def run(self, client, config) -> ProbeResult:
        try:
            import tiktoken
        except ImportError:
            return self.skip_result(
                "tiktoken not installed (pip install tiktoken)"
            )

        tolerance = config.probes.inflation.tolerance_tokens
        model = config.target.model
        max_tokens = config.target.max_tokens
        enc = tiktoken.get_encoding("cl100k_base")

        test_prompts = [
            "What is 2+2? Reply with only the number.",
            "Name one color of the rainbow.",
            "Say hello in French.",
        ]

        evidence: list[dict] = []
        inflated: list[dict] = []

        for prompt_text in test_prompts:
            messages = [{"role": "user", "content": prompt_text}]
            local_tokens = _count_message_tokens(enc, messages)

            result = await client.complete(
                model=model, messages=messages, max_tokens=max_tokens
            )
            if not result.success:
                evidence.append({"prompt": prompt_text, "error": result.error})
                continue

            reported = result.usage.get("prompt_tokens", 0)
            delta = reported - local_tokens
            entry = {
                "prompt": prompt_text,
                "local_tokens": local_tokens,
                "reported_tokens": reported,
                "delta": delta,
            }
            evidence.append(entry)
            if delta > tolerance:
                inflated.append(entry)

        if not evidence:
            return self.skip_result("all requests failed")

        if inflated:
            max_delta = max(e["delta"] for e in inflated)
            return self.fail_result(
                f"token inflation detected on {len(inflated)}/{len(test_prompts)} "
                f"prompts (max delta={max_delta}, tolerance={tolerance})",
                evidence=evidence,
                inflated_count=len(inflated),
                max_delta=max_delta,
                tolerance=tolerance,
            )

        return self.pass_result(
            f"all prompts within tolerance ({tolerance} tokens)",
            tolerance=tolerance,
        )


def _count_message_tokens(enc, messages: list[dict]) -> int:
    """Approximate token count for a messages array.

    Uses the Claude/OpenAI message framing heuristic:
    ~4 tokens per message for role/separators, plus content tokens.
    """
    total = 0
    for msg in messages:
        total += 4  # role + separators overhead
        total += len(enc.encode(msg.get("content", "")))
    total += 2  # reply priming
    return total
