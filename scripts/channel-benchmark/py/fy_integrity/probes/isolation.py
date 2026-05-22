"""P3: Cross-user cache isolation probe.

Detects cache leakage between users by checking if user B gets cached
tokens after user A sends the same prompt. Requires two different tokens.
"""

from __future__ import annotations

import asyncio
import uuid

from .base import BaseProbe, ProbeResult


class CrossUserCacheProbe(BaseProbe):
    name = "cross_user_cache_isolation"
    severity = "critical"

    async def run(self, client, config) -> ProbeResult:
        if not config.gateway.secondary_token:
            return self.skip_result("secondary_token not configured")

        from fy_integrity.client import IntegrityClient

        rounds = config.probes.isolation.rounds
        model = config.target.model
        max_tokens = config.target.max_tokens

        async with IntegrityClient(
            base_url=config.gateway.base_url,
            token=config.gateway.secondary_token,
            pin_channel_id=config.gateway.pin_channel_id,
            timeout_sec=config.target.request_timeout_sec,
        ) as client_b:
            evidence: list[dict] = []
            leaked_rounds = 0

            for i in range(rounds):
                unique_id = uuid.uuid4().hex
                prompt = f"Say 'ok'. Marker: {unique_id}"
                messages = [{"role": "user", "content": prompt}]

                result_a = await client.complete(
                    model=model, messages=messages, max_tokens=max_tokens
                )
                if not result_a.success:
                    evidence.append(
                        {"round": i, "error": f"user_a failed: {result_a.error}"}
                    )
                    continue

                await asyncio.sleep(1.0)

                result_b = await client_b.complete(
                    model=model, messages=messages, max_tokens=max_tokens
                )
                if not result_b.success:
                    evidence.append(
                        {"round": i, "error": f"user_b failed: {result_b.error}"}
                    )
                    continue

                cached_b = (
                    result_b.usage.get("prompt_tokens_details", {}).get(
                        "cached_tokens", 0
                    )
                    or 0
                )
                entry = {
                    "round": i,
                    "prompt_uuid": unique_id,
                    "user_a_prompt_tokens": result_a.usage.get("prompt_tokens", 0),
                    "user_b_cached_tokens": cached_b,
                    "leaked": cached_b > 0,
                }
                evidence.append(entry)
                if cached_b > 0:
                    leaked_rounds += 1

        if leaked_rounds > 0:
            return self.fail_result(
                f"cache leaked in {leaked_rounds}/{rounds} rounds "
                f"(user B got cached tokens from user A's prompt)",
                evidence=evidence,
                leaked_rounds=leaked_rounds,
                total_rounds=rounds,
            )

        return self.pass_result(
            f"no cross-user cache leakage in {rounds} rounds",
            total_rounds=rounds,
        )
