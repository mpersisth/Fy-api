"""P0: Cache integrity probe.

Detects hidden system prompt injection by verifying that fresh (never-seen)
prompts report zero cached tokens.
"""

from __future__ import annotations

import uuid

from .base import BaseProbe, ProbeResult


class CacheIntegrityProbe(BaseProbe):
    name = "cache_integrity"
    severity = "critical"

    async def run(self, client, config) -> ProbeResult:
        rounds = config.probes.cache.rounds
        model = config.target.model
        max_tokens = config.target.max_tokens

        fresh_hits: list[dict] = []
        evidence: list[dict] = []

        for i in range(rounds):
            unique_id = uuid.uuid4().hex
            prompt = f"Reply with exactly one word: hello. Request ID: {unique_id}"
            messages = [{"role": "user", "content": prompt}]

            result = await client.complete(
                model=model, messages=messages, max_tokens=max_tokens
            )
            if not result.success:
                evidence.append({"round": i, "error": result.error})
                continue

            cached = (
                result.usage.get("prompt_tokens_details", {}).get(
                    "cached_tokens", 0
                )
                or 0
            )
            entry = {
                "round": i,
                "prompt_uuid": unique_id,
                "prompt_tokens": result.usage.get("prompt_tokens", 0),
                "cached_tokens": cached,
            }
            evidence.append(entry)
            if cached > 0:
                fresh_hits.append(entry)

        if not evidence:
            return self.skip_result("all requests failed")

        if fresh_hits:
            return self.fail_result(
                f"cache_read > 0 on {len(fresh_hits)}/{rounds} fresh prompts "
                f"(max cached={max(e['cached_tokens'] for e in fresh_hits)})",
                evidence=evidence,
                fresh_cache_hits=len(fresh_hits),
                rounds=rounds,
            )

        return self.pass_result(
            f"all {rounds} fresh prompts had cache_read=0",
            rounds=rounds,
        )
