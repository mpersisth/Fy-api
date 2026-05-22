"""P1: Tool use passthrough probe.

Verifies that tool_call IDs from the upstream provider are passed through
without rewriting. Anthropic uses 'toolu_' prefix; rewriting to 'tooluse_'
indicates a middleman.
"""

from __future__ import annotations

from .base import BaseProbe, ProbeResult

_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "get_current_weather",
        "description": "Get the current weather in a given location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name",
                },
            },
            "required": ["location"],
        },
    },
}

EXPECTED_PREFIX = "toolu_"


class ToolUsePassthroughProbe(BaseProbe):
    name = "tool_use_passthrough"
    severity = "critical"

    async def run(self, client, config) -> ProbeResult:
        model = config.target.model
        messages = [
            {"role": "user", "content": "What's the weather in Tokyo?"}
        ]

        result = await client.complete(
            model=model,
            messages=messages,
            max_tokens=256,
            tools=[_TOOL_DEF],
        )

        if not result.success:
            return self.skip_result(f"request failed: {result.error}")

        tool_calls = result.tool_calls
        if not tool_calls:
            return self.skip_result(
                "model did not produce tool_calls (may not support tool use)"
            )

        evidence: list[dict] = []
        rewritten: list[str] = []

        for tc in tool_calls:
            tc_id = tc.get("id", "")
            entry = {
                "id": tc_id,
                "function": tc.get("function", {}).get("name", ""),
                "starts_with_expected": tc_id.startswith(EXPECTED_PREFIX),
            }
            evidence.append(entry)
            if not tc_id.startswith(EXPECTED_PREFIX):
                rewritten.append(tc_id)

        if rewritten:
            return self.fail_result(
                f"tool_call ID prefix rewritten: got '{rewritten[0][:12]}...' "
                f"(expected '{EXPECTED_PREFIX}')",
                evidence=evidence,
                rewritten_ids=rewritten,
                expected_prefix=EXPECTED_PREFIX,
            )

        return self.pass_result(
            f"all {len(tool_calls)} tool_call IDs have correct prefix",
            tool_call_count=len(tool_calls),
        )
