"""Layer 3: Prompt adherence — two-phase VLM judge evaluation.

Phase A (10 prompts): fast screening. pass_rate ≥ 80% → Phase B.
Phase B (20 prompts): deep evaluation. Only triggered if Phase A passes.
High-variance prompts are flagged and weighted ×0.5 in scoring.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

import httpx

from ..client import ImageClient, ImageResult
from ..config import Config, ChannelTarget


PHASE_A_PROMPTS = [
    {"name": "A01_object", "lang": "zh",
     "prompt": "一个红色的苹果放在白色木桌上",
     "criteria": "图片包含一个红色苹果和白色桌子"},
    {"name": "A02_color", "lang": "en",
     "prompt": "a bright blue sports car parked on a street",
     "criteria": "Image shows a blue car (not another color)"},
    {"name": "A03_counting", "lang": "zh",
     "prompt": "三只橘猫坐在一起",
     "criteria": "图片中有三只橘色猫"},
    {"name": "A04_style", "lang": "en",
     "prompt": "a mountain landscape in watercolor painting style",
     "criteria": "Image is in watercolor style (not photorealistic)"},
    {"name": "A05_text", "lang": "zh",
     "prompt": "一张海报，上面写着'欢迎光临'四个大字",
     "criteria": "图片中有可辨认的中文文字'欢迎光临'"},
    {"name": "A06_composition", "lang": "en",
     "prompt": "a coffee cup next to an open book on a desk",
     "criteria": "Image contains both a coffee cup and an open book"},
    {"name": "A07_culture", "lang": "zh", "high_variance": True,
     "prompt": "中国传统的红色灯笼挂在屋檐下",
     "criteria": "图片中有红色灯笼"},
    {"name": "A08_animal", "lang": "en",
     "prompt": "a golden retriever puppy playing in a garden",
     "criteria": "Image shows a golden retriever puppy in a garden"},
    {"name": "A09_scene", "lang": "zh",
     "prompt": "傍晚的阳光从窗户照进来",
     "criteria": "图片展示了阳光从窗户照入的场景"},
    {"name": "A10_abstract", "lang": "en",
     "prompt": "an abstract geometric pattern in pastel colors",
     "criteria": "Image shows abstract geometric shapes in soft pastel colors"},
]

PHASE_B_PROMPTS = [
    {"name": "B01_person", "lang": "zh", "high_variance": True,
     "prompt": "一位穿着汉服的女孩在花园里",
     "criteria": "图片中有穿着汉服的女性在花园场景中"},
    {"name": "B02_building", "lang": "en",
     "prompt": "the Eiffel Tower at sunset",
     "criteria": "Image shows the Eiffel Tower with sunset lighting"},
    {"name": "B03_food", "lang": "zh", "high_variance": True,
     "prompt": "一碗热气腾腾的兰州拉面",
     "criteria": "图片中有一碗面条"},
    {"name": "B04_text_en", "lang": "en",
     "prompt": "a road sign that says ROUTE 66 with desert background",
     "criteria": "Image shows a sign with 'ROUTE 66' text"},
    {"name": "B05_panda", "lang": "zh", "high_variance": True,
     "prompt": "一只大熊猫在吃竹子",
     "criteria": "图片中有一只大熊猫和竹子"},
    {"name": "B06_material", "lang": "en",
     "prompt": "a glass of water with ice cubes on marble surface",
     "criteria": "Image shows a glass with water and ice on marble"},
    {"name": "B07_lighting", "lang": "en",
     "prompt": "a candlelit dinner table with two wine glasses",
     "criteria": "Image shows candlelight atmosphere with wine glasses"},
    {"name": "B08_landmark", "lang": "zh",
     "prompt": "北京故宫的雪景",
     "criteria": "图片展示了故宫建筑在雪中的场景"},
    {"name": "B09_complex", "lang": "zh",
     "prompt": "一张办公桌，有笔记本、手机、咖啡杯和绿植",
     "criteria": "图片中有至少3个描述的物品（笔记本、手机、咖啡杯、绿植）"},
    {"name": "B10_sign", "lang": "en",
     "prompt": "a storefront sign that says OPEN in bold red letters",
     "criteria": "Image shows a sign with legible 'OPEN' text"},
    {"name": "B11_portrait", "lang": "en",
     "prompt": "a professional headshot of a woman in business attire",
     "criteria": "Image shows a professional-looking portrait"},
    {"name": "B12_inkwash", "lang": "zh",
     "prompt": "水墨画风格的山景",
     "criteria": "图片是水墨画风格的山景"},
    {"name": "B13_burger", "lang": "en",
     "prompt": "a juicy cheeseburger with fries on a wooden board",
     "criteria": "Image shows a burger with fries"},
    {"name": "B14_streetsign", "lang": "zh",
     "prompt": "一个路牌上面写着'中山路'，背景是城市街道",
     "criteria": "图片中有路牌和城市街道"},
    {"name": "B15_minimalist", "lang": "zh",
     "prompt": "极简主义的黑白几何图案",
     "criteria": "图片是黑白风格的几何图案"},
    {"name": "B16_cozy", "lang": "en",
     "prompt": "a cozy reading nook with armchair and bookshelf",
     "criteria": "Image shows a cozy reading space with chair and books"},
    {"name": "B17_glass", "lang": "zh",
     "prompt": "一个玻璃杯子里装半杯水，放在木桌上",
     "criteria": "图片中有玻璃杯和水"},
    {"name": "B18_citynight", "lang": "zh",
     "prompt": "夜晚的城市天际线，霓虹灯倒映在河面上",
     "criteria": "图片展示了夜景城市天际线和水面倒影"},
    {"name": "B19_cateye", "lang": "en",
     "prompt": "a close-up photo of a cat's green eyes",
     "criteria": "Image is a close-up showing cat eyes"},
    {"name": "B20_kitchen", "lang": "en",
     "prompt": "a kitchen scene with ingredients spread on a counter",
     "criteria": "Image shows a kitchen counter with various ingredients"},
]

PHASE_A_PASS_THRESHOLD = 0.80


@dataclass
class JudgeResult:
    prompt_name: str
    score: float
    passed: bool
    reasoning: str = ""
    raw_scores: list[float] = field(default_factory=list)
    stddev: float = 0.0
    high_variance: bool = False
    lang: str = ""
    is_high_variance_prompt: bool = False


@dataclass
class PhaseResult:
    phase: str
    results: list[JudgeResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    @property
    def weighted_pass_rate(self) -> float:
        if not self.results:
            return 0.0
        total_weight = 0.0
        weighted_pass = 0.0
        for r in self.results:
            w = 0.5 if r.is_high_variance_prompt else 1.0
            total_weight += w
            if r.passed:
                weighted_pass += w
        return weighted_pass / total_weight if total_weight > 0 else 0.0

    @property
    def zh_pass_rate(self) -> float:
        zh = [r for r in self.results if r.lang == "zh"]
        return sum(1 for r in zh if r.passed) / len(zh) if zh else 0.0

    @property
    def en_pass_rate(self) -> float:
        en = [r for r in self.results if r.lang == "en"]
        return sum(1 for r in en if r.passed) / len(en) if en else 0.0


@dataclass
class ChannelPromptResult:
    channel: ChannelTarget
    phase_a: PhaseResult = field(default_factory=lambda: PhaseResult(phase="A"))
    phase_b: PhaseResult | None = None
    phase_a_blocked: bool = False
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

    @property
    def judge_consistency(self) -> float:
        if not self.results:
            return 1.0
        consistent = sum(1 for r in self.results if not r.high_variance)
        return consistent / len(self.results)


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


async def run(cfg: Config, client: ImageClient) -> list[ChannelPromptResult]:
    pf_cfg = cfg.suites.prompt_follow
    if not pf_cfg.enabled:
        return []

    judge_base = pf_cfg.judge_base_url or cfg.gateway.base_url
    judge_token = pf_cfg.judge_token or cfg.gateway.user_token
    judge_repeat = pf_cfg.judge_repeat
    consistency_threshold = pf_cfg.consistency_threshold

    results = []
    async with httpx.AsyncClient(timeout=60.0) as judge_http:
        for ch in cfg.gateway.channels:
            cr = ChannelPromptResult(channel=ch)

            # Phase A: fast screening
            cr.phase_a = await _run_phase(
                "A", PHASE_A_PROMPTS, cfg, client, judge_http,
                judge_base, judge_token, judge_repeat, consistency_threshold, ch,
            )
            cr.results.extend(cr.phase_a.results)

            a_rate = cr.phase_a.weighted_pass_rate
            if a_rate < PHASE_A_PASS_THRESHOLD:
                cr.phase_a_blocked = True
            else:
                # Phase B: deep evaluation
                cr.phase_b = await _run_phase(
                    "B", PHASE_B_PROMPTS, cfg, client, judge_http,
                    judge_base, judge_token, judge_repeat, consistency_threshold, ch,
                )
                cr.results.extend(cr.phase_b.results)

            results.append(cr)
    return results


async def _run_phase(
    phase_name: str,
    prompts: list[dict],
    cfg: Config,
    client: ImageClient,
    judge_http: httpx.AsyncClient,
    judge_base: str,
    judge_token: str,
    judge_repeat: int,
    consistency_threshold: float,
    ch: ChannelTarget,
) -> PhaseResult:
    pr = PhaseResult(phase=phase_name)
    for case in prompts:
        body = {"model": cfg.model.name, "prompt": case["prompt"], "n": 1}
        try:
            r = await client.generate(body, pin_channel=ch.pin_channel_id)
        except Exception as e:
            pr.results.append(JudgeResult(
                case["name"], 0.0, False,
                f"generation exception: {type(e).__name__}: {str(e)[:100]}",
                lang=case.get("lang", ""),
                is_high_variance_prompt=case.get("high_variance", False),
            ))
            continue
        if not r.success:
            pr.results.append(JudgeResult(
                case["name"], 0.0, False,
                f"generation failed: {r.error[:100]}",
                lang=case.get("lang", ""),
                is_high_variance_prompt=case.get("high_variance", False),
            ))
            continue

        image_data = await _get_image_b64(client, r)
        if not image_data:
            pr.results.append(JudgeResult(
                case["name"], 0.0, False, "could not retrieve image",
                lang=case.get("lang", ""),
                is_high_variance_prompt=case.get("high_variance", False),
            ))
            continue

        raw_scores = []
        raw_reasonings = []
        for _ in range(judge_repeat):
            score, reasoning = await _judge_image(
                judge_http, judge_base, judge_token, cfg.suites.prompt_follow.judge_model,
                case["prompt"], case["criteria"], image_data,
                temperature=0.0,
            )
            raw_scores.append(score)
            raw_reasonings.append(reasoning)

        median_score = sorted(raw_scores)[len(raw_scores) // 2]
        sd = _stddev(raw_scores)
        hv = sd > consistency_threshold

        median_idx = raw_scores.index(median_score)
        pr.results.append(JudgeResult(
            prompt_name=case["name"],
            score=median_score,
            passed=median_score >= 0.6,
            reasoning=raw_reasonings[median_idx],
            raw_scores=raw_scores,
            stddev=sd,
            high_variance=hv,
            lang=case.get("lang", ""),
            is_high_variance_prompt=case.get("high_variance", False),
        ))
    return pr


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
    http: httpx.AsyncClient,
    base_url: str,
    token: str,
    model: str,
    prompt: str,
    criteria: str,
    image_b64: str,
    *,
    temperature: float = 0.0,
) -> tuple[float, str]:
    system_msg = (
        "You are an image quality judge. Score how well the image matches the criteria. "
        "Respond with ONLY a JSON object: {\"score\": 0.0-1.0, \"reasoning\": \"...\"}"
    )
    user_content = [
        {"type": "text", "text": f"Prompt: {prompt}\nCriteria: {criteria}\nScore 0.0-1.0:"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
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
                "temperature": temperature,
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
