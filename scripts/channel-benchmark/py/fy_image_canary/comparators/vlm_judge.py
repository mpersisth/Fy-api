"""VLM comparison judge — ask a VLM whether two images came from the same model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx


@dataclass
class VlmComparisonVerdict:
    prompt: str
    judge_verdict: str
    passed: bool
    reasoning: str
    raw_verdicts: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        if not self.raw_verdicts:
            return "low"
        agree = sum(1 for v in self.raw_verdicts if v == self.judge_verdict)
        ratio = agree / len(self.raw_verdicts)
        if ratio >= 0.9:
            return "high"
        if ratio >= 0.6:
            return "medium"
        return "low"


_SYSTEM_MSG = (
    "You are an expert image analyst. You are shown two images generated from "
    "the same text prompt by potentially different image generation models. "
    "Determine if both images appear to have been produced by the SAME model "
    "or DIFFERENT models. Focus on style, quality, texture, and rendering "
    "characteristics — NOT the specific content (which will differ by design). "
    "Respond with ONLY a JSON object: "
    '{\"verdict\": \"same\" or \"different\", \"reasoning\": \"...\"}'
)


async def evaluate_vlm_comparison(
    *,
    prompt: str,
    gateway_image_b64: str,
    vendor_image_b64: str,
    judge_base_url: str,
    judge_token: str,
    judge_model: str,
    repeat: int = 3,
) -> VlmComparisonVerdict:
    user_content = [
        {"type": "text", "text": (
            f"Original prompt: \"{prompt}\"\n"
            "Image A (left) and Image B (right) were generated from this prompt. "
            "Were they likely produced by the same model?"
        )},
        {"type": "image_url", "image_url": {
            "url": f"data:image/png;base64,{gateway_image_b64}"}},
        {"type": "image_url", "image_url": {
            "url": f"data:image/png;base64,{vendor_image_b64}"}},
    ]

    raw_verdicts: list[str] = []
    raw_reasonings: list[str] = []

    async with httpx.AsyncClient(timeout=90.0) as http:
        for _ in range(repeat):
            verdict, reasoning = await _single_judge_call(
                http, judge_base_url, judge_token, judge_model, user_content,
            )
            raw_verdicts.append(verdict)
            raw_reasonings.append(reasoning)

    same_count = sum(1 for v in raw_verdicts if v == "same")
    final_verdict = "same" if same_count > len(raw_verdicts) / 2 else "different"
    final_idx = raw_verdicts.index(final_verdict) if final_verdict in raw_verdicts else 0

    return VlmComparisonVerdict(
        prompt=prompt,
        judge_verdict=final_verdict,
        passed=(final_verdict == "same"),
        reasoning=raw_reasonings[final_idx] if raw_reasonings else "",
        raw_verdicts=raw_verdicts,
    )


async def _single_judge_call(
    http: httpx.AsyncClient,
    base_url: str,
    token: str,
    model: str,
    user_content: list,
) -> tuple[str, str]:
    try:
        resp = await http.post(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_MSG},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": 300,
                "temperature": 0.0,
            },
        )
        if resp.status_code != 200:
            return "uncertain", f"API error: {resp.status_code}"
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        return _parse_verdict(text)
    except Exception as e:
        return "uncertain", str(e)


def _parse_verdict(text: str) -> tuple[str, str]:
    try:
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        obj = json.loads(text.strip())
        verdict = str(obj.get("verdict", "uncertain")).lower()
        reasoning = str(obj.get("reasoning", ""))
        if verdict not in ("same", "different"):
            verdict = "uncertain"
        return verdict, reasoning
    except Exception:
        lower = text.lower()
        if "same" in lower and "different" not in lower:
            return "same", text[:150]
        if "different" in lower:
            return "different", text[:150]
        return "uncertain", text[:150]
