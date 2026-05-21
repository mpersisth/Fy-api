"""Layer 3: Prompt adherence — use VLM to judge if generated images match prompts."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

import httpx

from ..client import ImageClient
from ..config import Config, ChannelTarget


JUDGE_PROMPTS = [
    {
        "name": "simple_object",
        "prompt": "a red apple on a white wooden table",
        "criteria": "Image contains a red apple on a white table",
    },
    {
        "name": "color_accuracy",
        "prompt": "a bright blue sports car parked on a street",
        "criteria": "Image shows a blue car (not another color)",
    },
    {
        "name": "counting",
        "prompt": "exactly three orange cats sitting together",
        "criteria": "Image contains exactly three cats that are orange",
    },
    {
        "name": "style",
        "prompt": "a mountain landscape in watercolor painting style",
        "criteria": "Image is in watercolor style (not photorealistic)",
    },
    {
        "name": "composition",
        "prompt": "a coffee cup next to an open book on a desk",
        "criteria": "Image contains both a coffee cup and an open book",
    },
]


@dataclass
class JudgeResult:
    prompt_name: str
    score: float  # 0.0 - 1.0
    passed: bool
    reasoning: str = ""


@dataclass
class ChannelPromptResult:
    channel: ChannelTarget
    results: list[JudgeResult] = field(default_factory=list)

    @property
    def avg_score(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.score for r in self.results) / len(self.results)

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)


async def run(cfg: Config, client: ImageClient) -> list[ChannelPromptResult]:
    pf_cfg = cfg.suites.prompt_follow
    if not pf_cfg.enabled:
        return []

    judge_base = pf_cfg.judge_base_url or cfg.gateway.base_url
    judge_token = pf_cfg.judge_token or cfg.gateway.user_token

    results = []
    for ch in cfg.gateway.channels:
        cr = ChannelPromptResult(channel=ch)
        for case in JUDGE_PROMPTS[:pf_cfg.sample_count]:
            body = {"model": cfg.model.name, "prompt": case["prompt"], "n": 1}
            r = await client.generate(body, pin_channel=ch.pin_channel_id)
            if not r.success:
                cr.results.append(JudgeResult(
                    case["name"], 0.0, False, f"generation failed: {r.error[:100]}"))
                continue

            image_data = await _get_image_b64(client, r)
            if not image_data:
                cr.results.append(JudgeResult(
                    case["name"], 0.0, False, "could not retrieve image"))
                continue

            score, reasoning = await _judge_image(
                judge_base, judge_token, pf_cfg.judge_model,
                case["prompt"], case["criteria"], image_data,
            )
            cr.results.append(JudgeResult(
                case["name"], score, score >= 0.6, reasoning))
        results.append(cr)
    return results


async def _get_image_b64(client: ImageClient, r: ImageResult) -> str:
    if r.image_b64:
        return r.image_b64[0]
    if r.image_urls:
        try:
            data, _ = await client.download_image(r.image_urls[0])
            return base64.b64encode(data).decode()
        except Exception:
            return ""
    return ""


async def _judge_image(
    base_url: str,
    token: str,
    model: str,
    prompt: str,
    criteria: str,
    image_b64: str,
) -> tuple[float, str]:
    system_msg = (
        "You are an image quality judge. Score how well the image matches the criteria. "
        "Respond with ONLY a JSON object: {\"score\": 0.0-1.0, \"reasoning\": \"...\"}"
    )
    user_content = [
        {"type": "text", "text": f"Prompt: {prompt}\nCriteria: {criteria}\nScore 0.0-1.0:"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
    ]

    async with httpx.AsyncClient(timeout=60.0) as http:
        try:
            resp = await http.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_content},
                    ],
                    "max_tokens": 200,
                },
            )
            if resp.status_code != 200:
                return 0.0, f"judge API error: {resp.status_code}"
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return _parse_judge_response(text)
        except Exception as e:
            return 0.0, f"judge error: {e}"


def _parse_judge_response(text: str) -> tuple[float, str]:
    import json
    try:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        obj = json.loads(text)
        score = float(obj.get("score", 0.0))
        reasoning = str(obj.get("reasoning", ""))
        return min(max(score, 0.0), 1.0), reasoning
    except Exception:
        if any(w in text.lower() for w in ["1.0", "perfect", "excellent"]):
            return 0.8, text[:100]
        return 0.5, f"unparseable: {text[:100]}"
