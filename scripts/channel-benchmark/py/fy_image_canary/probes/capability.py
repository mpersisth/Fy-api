"""5B-3: Capability boundary probes.

4 public prompts + 4 internal (base64 encoded). VLM judge scoring.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

import httpx

from fy_image_conformance.client import ImageClient
from ..client import generate_and_download
from ..config import ImageCanaryConfig
from ..verdict import ProbeOutcome, CONFIDENCE_MEDIUM


PUBLIC_PROBES = [
    {"name": "precise_geometry",
     "prompt": "seven small circles arranged in a perfect hexagonal pattern on white background",
     "criteria": "Image shows exactly 7 circles in a hexagonal arrangement"},
    {"name": "physical_optics",
     "prompt": "a glass of water with a straw showing visible refraction, caustic light patterns on table",
     "criteria": "Image shows realistic light refraction through water"},
    {"name": "text_rendering",
     "prompt": "a wooden shop sign that clearly reads 'GRAND OPENING' in bold serif font",
     "criteria": "Text 'GRAND OPENING' is legible and correctly spelled"},
    {"name": "fine_detail",
     "prompt": "extreme macro photograph of a human eye showing iris texture, pupil reflection, and fine blood vessels",
     "criteria": "Image shows highly detailed iris texture and pupil"},
]

# Internal probes are base64-encoded to distinguish them from public probes in source code.
# This allows us to identify which prompts may have been seen during model training.
# Decoding happens at runtime.
_INTERNAL_PROBES_B64 = [
    base64.b64encode(json.dumps({
        "name": "long_prompt_coherence",
        "prompt": "A Victorian-era library with floor-to-ceiling mahogany bookshelves, "
                  "a green banker's lamp on an oak desk, an open leather-bound book, "
                  "reading glasses resting on it, dust motes in a beam of afternoon sunlight "
                  "from a tall arched window, a sleeping orange tabby cat on a velvet armchair",
        "criteria": "Image contains at least 5 of the 7 described elements: "
                    "bookshelves, lamp, desk, open book, glasses, window light, cat",
    }).encode()).decode(),
    base64.b64encode(json.dumps({
        "name": "composite_scene",
        "prompt": "split-screen image: left half is a snowy mountain peak under stars, "
                  "right half is a tropical beach at sunset, with a clear dividing line",
        "criteria": "Image shows two distinct scenes with a visible split or boundary",
    }).encode()).decode(),
    base64.b64encode(json.dumps({
        "name": "negative_space",
        "prompt": "minimalist black ink drawing of a single bare tree on pure white background, "
                  "tree takes up less than 30 percent of the frame, rest is empty white space",
        "criteria": "Image is minimalist with significant white/empty space",
    }).encode()).decode(),
    base64.b64encode(json.dumps({
        "name": "counting_complex",
        "prompt": "exactly five red roses and exactly three white daisies in a clear glass vase "
                  "on a blue tablecloth",
        "criteria": "Image contains approximately the correct count of flowers (5 red, 3 white)",
    }).encode()).decode(),
]


def _decode_internal_probes() -> list[dict]:
    result = []
    for b64_str in _INTERNAL_PROBES_B64:
        try:
            data = json.loads(base64.b64decode(b64_str))
            data["is_internal"] = True
            result.append(data)
        except Exception:
            continue
    return result


@dataclass
class CapabilityVerdict:
    probe_name: str
    score: float
    passed: bool
    reasoning: str
    is_internal: bool = False


async def run_capability_probes(
    client: ImageClient,
    cfg: ImageCanaryConfig,
) -> tuple[list[CapabilityVerdict], list[ProbeOutcome]]:
    all_probes = list(PUBLIC_PROBES) + _decode_internal_probes()
    judge_base = cfg.judge.base_url or cfg.gateway.base_url
    judge_token = cfg.judge.token or cfg.gateway.user_token

    verdicts: list[CapabilityVerdict] = []
    outcomes: list[ProbeOutcome] = []

    async with httpx.AsyncClient(timeout=90.0) as judge_http:
        for i, probe in enumerate(all_probes):
            body = {"model": cfg.gateway.model, "prompt": probe["prompt"], "n": 1}
            sample = await generate_and_download(
                client, body, pin_channel=cfg.gateway.pin_channel_id)

            if not sample.success or not sample.image_b64:
                v = CapabilityVerdict(
                    probe["name"], 0.0, False,
                    f"generation failed: {sample.error[:80]}",
                    is_internal=probe.get("is_internal", False),
                )
                verdicts.append(v)
                outcomes.append(ProbeOutcome(
                    probe_id=f"5b3-{probe['name']}", method="capability",
                    passed=False, score=0.0,
                    detail=v.reasoning,
                    confidence=CONFIDENCE_MEDIUM,
                ))
                continue

            score, reasoning = await _judge_capability(
                judge_http, judge_base, judge_token, cfg.judge.model,
                probe["prompt"], probe["criteria"], sample.image_b64,
            )
            v = CapabilityVerdict(
                probe["name"], score, score >= 0.6, reasoning,
                is_internal=probe.get("is_internal", False),
            )
            verdicts.append(v)
            outcomes.append(ProbeOutcome(
                probe_id=f"5b3-{probe['name']}", method="capability",
                passed=v.passed, score=v.score,
                detail=f"score={score:.2f}: {reasoning[:80]}",
                confidence=CONFIDENCE_MEDIUM,
            ))

    return verdicts, outcomes


async def _judge_capability(
    http: httpx.AsyncClient,
    base_url: str,
    token: str,
    model: str,
    prompt: str,
    criteria: str,
    image_b64: str,
) -> tuple[float, str]:
    system_msg = (
        "You are an image quality judge. Score how well the generated image "
        "matches the criteria on a 0.0-1.0 scale. "
        "Respond with ONLY a JSON object: {\"score\": 0.0-1.0, \"reasoning\": \"...\"}"
    )
    user_content = [
        {"type": "text", "text": f"Prompt: {prompt}\nCriteria: {criteria}"},
        {"type": "image_url", "image_url": {
            "url": f"data:image/png;base64,{image_b64}"}},
    ]
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
                "temperature": 0.0,
            },
        )
        if resp.status_code != 200:
            return 0.0, f"judge API error: {resp.status_code}"
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        return _parse_score(text)
    except Exception as e:
        return 0.0, str(e)


def _parse_score(text: str) -> tuple[float, str]:
    try:
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        obj = json.loads(text.strip())
        score = float(obj.get("score", 0.0))
        reasoning = str(obj.get("reasoning", ""))
        return min(max(score, 0.0), 1.0), reasoning
    except Exception:
        return 0.5, f"unparseable: {text[:100]}"
