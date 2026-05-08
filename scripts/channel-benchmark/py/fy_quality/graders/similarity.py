"""Embedding-cosine similarity grader.

Calls an embedding endpoint (OpenAI-compatible /v1/embeddings) once for
the channel's output and once for the reference, then cosines them.  We
cache the reference embedding across all channels (it doesn't change)
but not the per-channel output (it does).

Default threshold 0.80 is based on text-embedding-3-small: below 0.80 the
two texts are usually NOT paraphrases of each other for the kind of short
prompts we use; 0.85+ is strong paraphrase; 0.90+ is near-identical.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import httpx

from ..dataset import PromptRow
from . import GradeResult


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class EmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str = "text-embedding-3-small",
        timeout: float = 30.0,
    ):
        self._url = base_url.rstrip("/") + "/v1/embeddings"
        self._model = model
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=timeout, write=10, pool=10),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        self._cache: dict[str, list[float]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "EmbeddingClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def embed(self, text: str) -> tuple[list[float], int]:
        """Return (embedding, tokens_used). Cached by exact text match."""
        if text in self._cache:
            return self._cache[text], 0
        body = {"model": self._model, "input": text}
        try:
            resp = await self._client.post(self._url, json=body)
        except httpx.HTTPError:
            return [], 0
        if resp.status_code >= 400:
            return [], 0
        data = resp.json()
        items = data.get("data") or []
        if not items:
            return [], 0
        vec = list(items[0].get("embedding") or [])
        tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
        self._cache[text] = vec
        return vec, tokens


@dataclass
class SimilarityGrader:
    client: EmbeddingClient | None = None
    pass_threshold: float = 0.80
    name: str = "similarity"

    async def grade(self, row: PromptRow, output: str) -> GradeResult:
        if self.client is None:
            return GradeResult(False, 0.0, "no embedding client configured")
        if not row.reference:
            return GradeResult(False, 0.0, "similarity grader requires 'reference'")

        out_vec, out_tok = await self.client.embed(output)
        if not out_vec:
            return GradeResult(False, 0.0, "failed to embed output", judge_tokens=out_tok)
        ref_vec, ref_tok = await self.client.embed(row.reference)
        if not ref_vec:
            return GradeResult(False, 0.0, "failed to embed reference", judge_tokens=out_tok + ref_tok)

        sim = cosine(out_vec, ref_vec)
        ok = sim >= self.pass_threshold
        return GradeResult(
            passed=ok,
            score=max(0.0, min(1.0, sim)),  # clamp to [0,1]
            detail=f"cosine={sim:.3f} threshold={self.pass_threshold}",
            judge_tokens=out_tok + ref_tok,
        )
