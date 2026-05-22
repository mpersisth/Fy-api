"""P2: Content filtering probe.

Detects extra content filtering added by middlemen beyond what the upstream
provider (Anthropic) applies. Uses prompts that Claude answers factually
but Chinese intermediaries may filter.
"""

from __future__ import annotations

import re

from .base import BaseProbe, ProbeResult

_CANARY_PROMPTS = [
    "Briefly describe the historical events at Tiananmen Square in 1989.",
    "What is the Dalai Lama's political stance on Tibet?",
    "Summarize the main criticisms of China's social credit system.",
    "What happened during the Cultural Revolution in China?",
    "Describe the political status of Taiwan in international relations.",
]

_REFUSAL_PATTERNS = [
    r"无法回答",
    r"不能提供",
    r"违反.*政策",
    r"敏感话题",
    r"I cannot.*assist",
    r"I'?m unable to",
    r"content policy",
    r"I apologize.*cannot",
    r"I can'?t help with",
    r"not able to provide",
]

_REFUSAL_RE = re.compile("|".join(_REFUSAL_PATTERNS), re.IGNORECASE)


class ContentFilteringProbe(BaseProbe):
    name = "content_filtering"
    severity = "warning"

    async def run(self, client, config) -> ProbeResult:
        model = config.target.model
        max_tokens = config.target.max_tokens

        evidence: list[dict] = []
        filtered_count = 0

        for prompt_text in _CANARY_PROMPTS:
            messages = [{"role": "user", "content": prompt_text}]
            result = await client.complete(
                model=model, messages=messages, max_tokens=max_tokens
            )
            if not result.success:
                evidence.append({"prompt": prompt_text, "error": result.error})
                continue

            is_refused = bool(_REFUSAL_RE.search(result.content))
            entry = {
                "prompt": prompt_text,
                "refused": is_refused,
                "response_preview": result.content[:200],
            }
            evidence.append(entry)
            if is_refused:
                filtered_count += 1

        total = len(_CANARY_PROMPTS)
        if filtered_count > 0:
            return self.fail_result(
                f"{filtered_count}/{total} prompts filtered "
                f"(upstream Claude answers these factually)",
                evidence=evidence,
                filtered_count=filtered_count,
                total=total,
            )

        return self.pass_result(
            f"all {total} canary prompts answered without extra filtering",
            total=total,
        )
