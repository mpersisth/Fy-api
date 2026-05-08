"""Quality runner: for each channel × prompt, call the channel, grade, aggregate.

A shared on-disk cache keyed by sha256(channel_name, model, prompt, params)
short-circuits repeat runs — rerunning the suite after a config tweak
costs only the new prompts. Cache lives under config.cache_dir.

We do NOT reuse fy_loadtest.ChatClient here because that client is tuned
for load measurement (discards generated text, only keeps byte counts and
usage). Quality grading needs the full assistant text, so we issue plain
non-streaming requests with httpx directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from .config import Channel, QualityConfig
from .dataset import PromptRow, load_jsonl
from .graders import GradeResult, Grader
from .graders.deterministic import (
    ContainsGrader,
    ExactGrader,
    JsonSchemaGrader,
    RegexGrader,
)
from .graders.pairwise import PairwiseGrader
from .graders.rubric import RubricGrader
from .graders.similarity import EmbeddingClient, SimilarityGrader
from .judge_client import JudgeClient


@dataclass
class PromptResult:
    channel: str
    model: str
    prompt_id: str
    category: str
    grader: str
    passed: bool
    score: float
    detail: str
    output: str
    output_tokens: int
    prompt_tokens: int
    judge_tokens: int
    elapsed_s: float
    cached: bool
    error: str = ""


@dataclass
class QualityReport:
    generated_at_unix: float
    channels: list[str]
    dataset_path: str
    per_prompt: list[PromptResult] = field(default_factory=list)


class QualityRunner:
    def __init__(self, cfg: QualityConfig):
        self.cfg = cfg
        self._cache_dir = Path(cfg.cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._dataset: list[PromptRow] = load_jsonl(cfg.dataset, kind="quality")

    async def run(self) -> QualityReport:
        judges = [
            JudgeClient(
                base_url=j.base_url, api_key=j.api_key,
                model=j.model, label=j.label,
                timeout=self.cfg.request_timeout_sec,
            )
            for j in self.cfg.judges
        ]
        emb = (
            EmbeddingClient(
                base_url=self.cfg.embedding.base_url,
                api_key=self.cfg.embedding.api_key,
                model=self.cfg.embedding.model,
            )
            if self.cfg.embedding
            else None
        )
        graders = self._build_graders(judges, emb)

        report = QualityReport(
            generated_at_unix=time.time(),
            channels=[c.name for c in self.cfg.channels],
            dataset_path=self.cfg.dataset,
        )

        try:
            sem = asyncio.Semaphore(self.cfg.concurrency)

            for ch in self.cfg.channels:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        connect=10, read=self.cfg.request_timeout_sec,
                        write=30, pool=10,
                    ),
                    headers={
                        "Authorization": f"Bearer {ch.token}",
                        "Content-Type": "application/json",
                    },
                ) as http:
                    async def one(row: PromptRow) -> PromptResult:
                        async with sem:
                            return await self._grade_one(ch, row, http, graders)

                    results = await asyncio.gather(*[one(r) for r in self._dataset])
                report.per_prompt.extend(results)
        finally:
            for j in judges:
                await j.aclose()
            if emb:
                await emb.aclose()

        return report

    def _build_graders(
        self, judges: list[JudgeClient], emb: EmbeddingClient | None
    ) -> dict[str, Grader]:
        return {
            "exact": ExactGrader(),
            "regex": RegexGrader(),
            "contains": ContainsGrader(),
            "json_schema": JsonSchemaGrader(),
            "rubric": RubricGrader(judges=judges, pass_score=self.cfg.pass_score),
            "similarity": SimilarityGrader(client=emb, pass_threshold=self.cfg.similarity_threshold),
            "pairwise": PairwiseGrader(judge=judges[0] if judges else None),
        }

    async def _grade_one(
        self, ch: Channel, row: PromptRow,
        http: httpx.AsyncClient, graders: dict[str, Grader],
    ) -> PromptResult:
        cache_key = _gen_cache_key(
            ch.name, ch.model, row.prompt, row.max_tokens, row.temperature,
            row.system,
        )
        cache_path = self._cache_dir / f"{cache_key}.json"
        cached = False
        output, output_tokens, prompt_tokens, elapsed_s, error = "", 0, 0, 0.0, ""

        if cache_path.exists():
            try:
                blob = json.loads(cache_path.read_text(encoding="utf-8"))
                output = blob["output"]
                output_tokens = int(blob.get("output_tokens", 0))
                prompt_tokens = int(blob.get("prompt_tokens", 0))
                elapsed_s = float(blob.get("elapsed_s", 0.0))
                error = blob.get("error", "") or ""
                cached = True
            except (json.JSONDecodeError, KeyError):
                cached = False

        if not cached:
            t0 = time.monotonic()
            output, output_tokens, prompt_tokens, error = await _generate(http, ch, row)
            elapsed_s = time.monotonic() - t0
            cache_path.write_text(json.dumps({
                "output": output,
                "output_tokens": output_tokens,
                "prompt_tokens": prompt_tokens,
                "elapsed_s": elapsed_s,
                "error": error,
            }, ensure_ascii=False))

        if error:
            grade = GradeResult(False, 0.0, f"request failed: {error}")
        else:
            grader = graders.get(row.grader)
            if grader is None:
                grade = GradeResult(False, 0.0, f"unknown grader: {row.grader!r}")
            else:
                grade = await grader.grade(row, output)

        return PromptResult(
            channel=ch.name, model=ch.model, prompt_id=row.id,
            category=row.category, grader=row.grader,
            passed=grade.passed, score=grade.score, detail=grade.detail,
            output=output, output_tokens=output_tokens, prompt_tokens=prompt_tokens,
            judge_tokens=grade.judge_tokens,
            elapsed_s=elapsed_s, cached=cached, error=error,
        )


async def _generate(
    http: httpx.AsyncClient, ch: Channel, row: PromptRow,
) -> tuple[str, int, int, str]:
    """Call /v1/chat/completions non-stream. Returns (text, out_tokens, in_tokens, err)."""
    messages: list[dict] = []
    if row.system:
        messages.append({"role": "system", "content": row.system})
    messages.append({"role": "user", "content": row.prompt})

    body = {
        "model": ch.model,
        "messages": messages,
        "max_tokens": row.max_tokens if row.max_tokens is not None else 256,
        "temperature": row.temperature if row.temperature is not None else 0.0,
        "stream": False,
    }
    url = ch.base_url.rstrip("/") + "/v1/chat/completions"
    try:
        resp = await http.post(url, json=body)
    except httpx.HTTPError as e:
        return "", 0, 0, f"http: {e}"

    if resp.status_code >= 400:
        return "", 0, 0, f"HTTP {resp.status_code}: {resp.text[:300]}"

    try:
        data = resp.json()
    except ValueError as e:
        return "", 0, 0, f"bad json: {e}"

    choices = data.get("choices") or []
    if not choices:
        return "", 0, 0, "no choices in response"
    text = (choices[0].get("message") or {}).get("content", "") or ""
    usage = data.get("usage") or {}
    return (
        text,
        int(usage.get("completion_tokens") or 0),
        int(usage.get("prompt_tokens") or 0),
        "",
    )


def _gen_cache_key(
    channel: str, model: str, prompt: str,
    max_tokens: int | None, temperature: float | None,
    system: str | None,
) -> str:
    h = hashlib.sha256()
    for part in (channel, model, prompt, str(max_tokens), str(temperature), system or ""):
        h.update(part.encode())
        h.update(b"\x00")
    return h.hexdigest()[:32]


def report_to_dict(r: QualityReport) -> dict:
    return {
        "generated_at_unix": r.generated_at_unix,
        "channels": r.channels,
        "dataset_path": r.dataset_path,
        "per_prompt": [asdict(p) for p in r.per_prompt],
    }
