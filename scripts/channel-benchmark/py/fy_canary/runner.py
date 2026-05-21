"""Canary runner with two modes:

  - `baseline`: hit the trusted source, save outputs + embeddings per prompt.
  - `audit`:    hit the channel under test, compare to saved baseline.

The same runner class handles both; only the post-processing differs.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field

import httpx

from fy_quality.dataset import PromptRow, load_jsonl
from fy_quality.graders.similarity import EmbeddingClient

from . import alignment, drift, mmd
from .baseline import BaselineStore, ChannelBaseline, ProbeBaseline
from .client import CanaryClient
from .config import CanaryConfig


@dataclass
class ProbeOutcome:
    prompt_id: str
    method: str             # "alignment" | "drift" | "mmd"
    passed: bool
    score: float            # method-specific, 0-1
    detail: str


@dataclass
class CanaryReport:
    mode: str                      # "baseline" or "audit"
    source_name: str
    model: str
    generated_at_unix: float
    outcomes: list[ProbeOutcome] = field(default_factory=list)


class CanaryRunner:
    def __init__(self, cfg: CanaryConfig):
        self.cfg = cfg
        self.dataset: list[PromptRow] = load_jsonl(cfg.dataset, kind="canary")
        self.store = BaselineStore(cfg.baselines_dir)

    def baseline_health(self) -> dict | None:
        """Return baseline metadata + staleness verdict, or None if no baseline.

        Output:
            {
              "exists": bool,
              "age_days": float,
              "stale": bool,
              "max_age_days": int,
              "n_probes": int,
              "total_samples": int,
              "recorded_at_iso": str,
              "fy_canary_version": str,
            }
        """
        b = self.store.load(self.cfg.source.name)
        if b is None:
            return None
        max_age = self.cfg.baseline_max_age_days
        return {
            "exists": True,
            "age_days": round(b.age_days, 2),
            "stale": b.age_days > max_age,
            "max_age_days": max_age,
            "n_probes": b.n_probes,
            "total_samples": b.total_samples,
            "recorded_at_iso": b.recorded_at_iso,
            "fy_canary_version": b.fy_canary_version,
        }

    # --------- baseline mode --------------------------------------------------
    async def build_baseline(self) -> ChannelBaseline:
        """Collect N samples per prompt from the configured source and persist."""
        probes: dict[str, ProbeBaseline] = {}
        emb_client = self._maybe_emb_client()

        try:
            async with CanaryClient(
                base_url=self.cfg.source.base_url,
                api_key=self.cfg.source.api_key,
                timeout=self.cfg.request_timeout_sec,
                pin_channel_id=self.cfg.source.pin_channel_id,
            ) as client:
                sem = asyncio.Semaphore(self.cfg.concurrency)

                async def one_probe(row: PromptRow) -> ProbeBaseline:
                    async with sem:
                        return await self._build_one_baseline(row, client, emb_client)

                results = await asyncio.gather(*(one_probe(r) for r in self.dataset))
                for p in results:
                    probes[p.prompt_id] = p
        finally:
            if emb_client:
                await emb_client.aclose()

        baseline = ChannelBaseline(
            source_name=self.cfg.source.name,
            model=self.cfg.source.model,
            created_at_unix=time.time(),
            probes=probes,
        )
        self.store.save(baseline)
        return baseline

    async def _build_one_baseline(
        self, row: PromptRow, client: CanaryClient, emb: EmbeddingClient | None,
    ) -> ProbeBaseline:
        method = row.method or "alignment"
        n = self._n_samples_for(row, method)
        samples = await self._sample_n(client, row, n)
        centroid_vec: list[float] | None = None
        if method == "drift" and emb and samples:
            vecs = [(await emb.embed(s))[0] for s in samples]
            vecs = [v for v in vecs if v]
            if vecs:
                centroid_vec = drift.centroid(vecs)
        return ProbeBaseline(
            prompt_id=row.id,
            method=method,
            samples=samples,
            centroid=centroid_vec,
        )

    # --------- audit mode -----------------------------------------------------
    async def audit(self) -> CanaryReport:
        """Compare current source against stored baseline."""
        baseline = self.store.load(self.cfg.source.name)
        if baseline is None:
            raise FileNotFoundError(
                f"no baseline found at {self.store.path_for(self.cfg.source.name)} — "
                f"run `fy-canary baseline -c ...` first."
            )

        emb = self._maybe_emb_client()
        outcomes: list[ProbeOutcome] = []

        try:
            async with CanaryClient(
                base_url=self.cfg.source.base_url,
                api_key=self.cfg.source.api_key,
                timeout=self.cfg.request_timeout_sec,
                pin_channel_id=self.cfg.source.pin_channel_id,
            ) as client:
                sem = asyncio.Semaphore(self.cfg.concurrency)

                async def one(row: PromptRow) -> ProbeOutcome | None:
                    async with sem:
                        return await self._audit_one(row, baseline, client, emb)

                for row in self.dataset:
                    o = await one(row)
                    if o is not None:
                        outcomes.append(o)
        finally:
            if emb:
                await emb.aclose()

        return CanaryReport(
            mode="audit",
            source_name=self.cfg.source.name,
            model=self.cfg.source.model,
            generated_at_unix=time.time(),
            outcomes=outcomes,
        )

    # --------- verify-baseline mode -------------------------------------------
    async def verify_baseline(self) -> CanaryReport:
        """Re-record a fresh mini-baseline against the same source and compare
        to the stored one.

        This catches "the baseline itself has drifted" — i.e. the vendor model
        has been updated, the system prompt template has changed, the API has
        been silently swapped under us. It uses identical alignment + drift
        logic as audit, but the "current" samples come from re-querying the
        SAME source the baseline was recorded from. A divergence here means
        you should re-record the baseline.
        """
        baseline = self.store.load(self.cfg.source.name)
        if baseline is None:
            raise FileNotFoundError(
                f"no baseline found at {self.store.path_for(self.cfg.source.name)} — "
                f"nothing to verify."
            )

        emb = self._maybe_emb_client()
        outcomes: list[ProbeOutcome] = []

        try:
            async with CanaryClient(
                base_url=self.cfg.source.base_url,
                api_key=self.cfg.source.api_key,
                timeout=self.cfg.request_timeout_sec,
                pin_channel_id=self.cfg.source.pin_channel_id,
            ) as client:
                sem = asyncio.Semaphore(self.cfg.concurrency)

                async def one(row: PromptRow) -> ProbeOutcome | None:
                    async with sem:
                        # verify_baseline reuses _audit_one — the comparison
                        # logic is identical; only interpretation of failure
                        # differs (here: rebuild baseline; in audit: suspect
                        # channel substitution).
                        return await self._audit_one(row, baseline, client, emb)

                for row in self.dataset:
                    o = await one(row)
                    if o is not None:
                        outcomes.append(o)
        finally:
            if emb:
                await emb.aclose()

        return CanaryReport(
            mode="verify-baseline",
            source_name=self.cfg.source.name,
            model=self.cfg.source.model,
            generated_at_unix=time.time(),
            outcomes=outcomes,
        )

    async def _audit_one(
        self, row: PromptRow,
        baseline: ChannelBaseline,
        client: CanaryClient,
        emb: EmbeddingClient | None,
    ) -> ProbeOutcome | None:
        b = baseline.probes.get(row.id)
        if b is None:
            return ProbeOutcome(
                prompt_id=row.id, method=row.method or "alignment",
                passed=False, score=0.0,
                detail=f"no baseline for {row.id!r}; rebuild baseline",
            )

        method = b.method
        if method == "alignment":
            if not b.samples:
                return ProbeOutcome(row.id, method, False, 0.0, "baseline has no samples")
            sample = await self._sample_n(client, row, 1)
            current = sample[0] if sample else ""
            v = alignment.evaluate_alignment(
                prompt_id=row.id,
                baseline_sample=b.samples[0],
                current_sample=current,
                threshold=0.70,
            )
            return ProbeOutcome(
                row.id, method, v.passed, v.similarity,
                f"edit-sim={v.similarity:.3f} threshold={v.threshold:.2f}",
            )

        if method == "drift":
            if b.centroid is None or emb is None:
                return ProbeOutcome(
                    row.id, method, False, 0.0,
                    "baseline missing centroid or embedding client not configured",
                )
            n = self._n_samples_for(row, method)
            samples = await self._sample_n(client, row, n)
            if not samples:
                return ProbeOutcome(row.id, method, False, 0.0, "no current samples")
            vecs_raw = [(await emb.embed(s))[0] for s in samples]
            vecs = [v for v in vecs_raw if v]
            if not vecs:
                return ProbeOutcome(row.id, method, False, 0.0, "failed to embed current samples")
            current_centroid = drift.centroid(vecs)
            v = drift.evaluate_drift(
                prompt_id=row.id,
                baseline_centroid=b.centroid,
                current_centroid=current_centroid,
                n_samples=len(vecs),
                threshold=0.93,
            )
            return ProbeOutcome(
                row.id, method, v.passed, v.similarity,
                f"centroid-cos={v.similarity:.3f} n={v.n_samples} threshold={v.threshold:.2f}",
            )

        if method == "mmd":
            if not self.cfg.mmd_enabled:
                return ProbeOutcome(row.id, method, True, 1.0, "mmd disabled in config")
            if not mmd.mmd_available():
                return ProbeOutcome(
                    row.id, method, True, 1.0,
                    "mmd skipped: install extras `[canary]`",
                )
            if not b.samples:
                return ProbeOutcome(row.id, method, False, 0.0, "baseline has no samples")
            n = self._n_samples_for(row, method)
            current_samples = await self._sample_n(client, row, n)
            if not current_samples:
                return ProbeOutcome(row.id, method, False, 0.0, "no current samples")
            v = mmd.evaluate_mmd(
                prompt_id=row.id,
                baseline_samples=b.samples,
                current_samples=current_samples,
                alpha=0.01,
            )
            return ProbeOutcome(
                row.id, method, v.passed, float(1.0 - v.p_value),  # invert for "divergence strength"
                f"mmd p={v.p_value:.4f} stat={v.statistic:.4f} alpha={v.alpha}",
            )

        return ProbeOutcome(row.id, method, False, 0.0, f"unknown method: {method!r}")

    # --------- shared helpers -------------------------------------------------
    async def _sample_n(self, client: CanaryClient, row: PromptRow, n: int) -> list[str]:
        """Concurrently fetch N completions for one prompt."""
        max_tokens = row.max_tokens if row.max_tokens is not None else 200
        temperature = row.temperature if row.temperature else None
        tasks = [
            client.complete(
                model=self.cfg.source.model,
                prompt=row.prompt,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            for _ in range(n)
        ]
        out = await asyncio.gather(*tasks)
        return [s for s in out if s]

    def _n_samples_for(self, row: PromptRow, method: str) -> int:
        if row.n_samples is not None:
            return row.n_samples
        return self.cfg.mmd_n_samples if method in {"mmd", "drift"} else 1

    def _maybe_emb_client(self) -> EmbeddingClient | None:
        if self.cfg.embedding is None:
            return None
        return EmbeddingClient(
            base_url=self.cfg.embedding.base_url,
            api_key=self.cfg.embedding.api_key,
            model=self.cfg.embedding.model,
        )


def report_to_dict(r: CanaryReport) -> dict:
    return {
        "mode": r.mode,
        "source_name": r.source_name,
        "model": r.model,
        "generated_at_unix": r.generated_at_unix,
        "outcomes": [asdict(o) for o in r.outcomes],
    }
