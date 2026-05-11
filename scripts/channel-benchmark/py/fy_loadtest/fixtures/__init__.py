"""Long-reasoning prompt fixtures used by the long-thinking loadtest preset.

These prompts intentionally need 5-30 minutes of model time on a thinking
model (kimi-k2-thinking, deepseek-r1, etc.). They were chosen to mirror the
2026-05-11 customer incident where aime25 / gpqa-diamond benchmarks were
truncated by Fy-api's `RELAY_TIMEOUT=600s`.

Each prompt is paraphrased from publicly-known competition/exam problems.
We do NOT use the *exact* AIME/GPQA prompts — those are presumed to be in
training corpora, so any 'pass' on them tells us nothing about correctness.
The only thing we want to assert is `request runs to completion`, which
hinges on the gateway's timeout chain rather than the model's accuracy.

Module is import-safe (no I/O at import time) so it's cheap to reuse from
both the loadtest CLI and the conformance test suite.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LongReasoningPrompt:
    """A single long-reasoning fixture.

    `id` is stable so reports can name it. `expected_min_seconds` is the
    minimum wall-clock time we expect a real thinking model to spend on it;
    if a reply comes back faster than that, we're not actually exercising
    the long-reasoning path (the model fell back to a non-thinking variant
    or short-circuited the answer).
    """

    id: str
    prompt: str
    expected_min_seconds: float
    category: str


# Three fixtures, three flavours of long reasoning.
LONG_REASONING_PROMPTS: tuple[LongReasoningPrompt, ...] = (
    LongReasoningPrompt(
        id="aime-style-combinatorics",
        category="aime-style",
        # AIME-style: combinatorial enumeration with a non-obvious symmetry.
        # A thinking model typically spends 3-8 minutes on this kind of question.
        prompt=(
            "Find the number of ordered triples (a, b, c) of positive integers "
            "with a < b < c, gcd(a, b, c) = 1, and a + b + c = 100, such that "
            "no two of a, b, c are congruent modulo 7. Show every step of your "
            "reasoning, then state the final integer answer on its own line as "
            "ANSWER: <number>."
        ),
        expected_min_seconds=60.0,
    ),
    LongReasoningPrompt(
        id="gpqa-style-physics",
        category="gpqa-style",
        # GPQA-style: graduate-level physics word problem requiring multi-step
        # derivation and unit chasing. 5-15 minutes on a thinking model.
        prompt=(
            "A spherical conducting shell of radius R carries total charge Q. "
            "A point dipole p is fixed at the center, oriented along z. The "
            "shell is grounded. Derive: (1) the induced surface charge density "
            "as a function of polar angle, (2) the total work done by the "
            "external agent in slowly bringing the dipole from infinity to the "
            "center, and (3) the leading-order correction if the shell has "
            "finite resistance r per square. Use Gaussian units; show every "
            "intermediate step. End with FINAL ANSWERS: (1) ... (2) ... (3) ..."
        ),
        expected_min_seconds=120.0,
    ),
    LongReasoningPrompt(
        id="proof-style-number-theory",
        category="proof",
        # Proof-style: open-ended chain-of-thought with no clean numeric answer.
        # Forces the model to reason for >10 minutes if it takes the prompt
        # seriously. Most likely to surface upstream-side stream stalls.
        prompt=(
            "Prove that for every prime p > 3, there exist infinitely many "
            "integers n such that n^2 + 1 has at least one prime factor "
            "congruent to 1 mod p, and characterize the density of such n. "
            "Be rigorous: state every lemma you depend on, give a fully-stated "
            "proof, and conclude with a quantitative density estimate. End "
            "with QED on its own line."
        ),
        expected_min_seconds=180.0,
    ),
)


def by_id(prompt_id: str) -> LongReasoningPrompt:
    """Look up a fixture by id. Raises KeyError for unknown ids — fail loud."""
    for p in LONG_REASONING_PROMPTS:
        if p.id == prompt_id:
            return p
    raise KeyError(
        f"unknown long-reasoning prompt id {prompt_id!r}; known: "
        f"{[p.id for p in LONG_REASONING_PROMPTS]}"
    )
