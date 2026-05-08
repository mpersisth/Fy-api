"""Pairwise A-vs-B comparison grader with position-bias mitigation.

For each test case we ask a judge model: "Which response better follows
the instructions?" We run the comparison TWICE with positions swapped
(A/B then B/A) and only award a clear win when the judge picks the same
response both times. Ties or flips count as a tie.

This pattern matches MT-Bench's pairwise methodology and is the single
most-effective mitigation for the "judges prefer whichever response is
shown first" bias.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..dataset import PromptRow
from ..judge_client import JudgeClient
from . import GradeResult

_PAIRWISE_TEMPLATE = """You are comparing two AI assistants' answers.

[Prompt both assistants received]
{prompt}

[Assistant {label_a}'s answer]
{answer_a}

[Assistant {label_b}'s answer]
{answer_b}

Which assistant gave the better answer? Judge on correctness, relevance,
and instruction-following. Ignore verbosity and formatting differences.

Output format (strict):
- One line of brief reasoning (under 30 words).
- Then a line containing ONLY "WINNER: A" or "WINNER: B" or "WINNER: TIE".
"""

_WINNER_RE = re.compile(r"^\s*WINNER:\s*(A|B|TIE)\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass
class PairwiseResult:
    winner: str      # "channel", "reference", or "tie"
    raw_a_first: str
    raw_b_first: str
    tokens_used: int


@dataclass
class PairwiseGrader:
    """Compare channel output vs. reference. Pass = channel is at least a tie."""

    judge: JudgeClient | None = None
    name: str = "pairwise"

    async def grade(self, row: PromptRow, output: str) -> GradeResult:
        if self.judge is None:
            return GradeResult(False, 0.0, "no judge configured")
        if not row.reference:
            return GradeResult(False, 0.0, "pairwise grader requires 'reference' (baseline answer)")

        # Round 1: channel=A, reference=B.
        v1 = await self.judge.judge(_PAIRWISE_TEMPLATE.format(
            prompt=row.prompt, label_a="A", label_b="B",
            answer_a=output, answer_b=row.reference,
        ))
        # Round 2: swapped — channel=B, reference=A.
        v2 = await self.judge.judge(_PAIRWISE_TEMPLATE.format(
            prompt=row.prompt, label_a="A", label_b="B",
            answer_a=row.reference, answer_b=output,
        ))

        r1 = _parse_winner(v1.raw)
        r2 = _parse_winner(v2.raw)
        tokens = v1.tokens_used + v2.tokens_used

        if r1 is None or r2 is None:
            return GradeResult(
                False, 0.0,
                f"unparseable verdicts: r1={r1!r} r2={r2!r}",
                judge_tokens=tokens,
            )

        # Translate to "who really won" — channel was A in r1 and B in r2.
        ch_won_r1 = r1 == "A"
        ref_won_r1 = r1 == "B"
        ch_won_r2 = r2 == "B"
        ref_won_r2 = r2 == "A"

        if ch_won_r1 and ch_won_r2:
            result = "channel"; score = 1.0; passed = True
        elif ref_won_r1 and ref_won_r2:
            result = "reference"; score = 0.0; passed = False
        else:
            # Either TIE somewhere, or flip between rounds — call it tie.
            result = "tie"; score = 0.5; passed = True

        return GradeResult(
            passed=passed,
            score=score,
            detail=f"pairwise={result} (r1={r1}, r2={r2})",
            judge_tokens=tokens,
        )


def _parse_winner(text: str) -> str | None:
    m = _WINNER_RE.search(text)
    if not m:
        return None
    return m.group(1).upper()
