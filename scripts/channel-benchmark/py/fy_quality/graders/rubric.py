"""LLM-as-judge rubric grader.

Dual-judge mode is the default for Fy-api: we call two different judge
models with the same rubric and only pass the test if BOTH agree the output
meets the bar.  This cuts false-positive rate vs. trusting one judge, at
the cost of 2× judge spend.  When only one judge is configured we fall
back to single-judge mode.

The rubric prompt asks the judge to output a score 1-5 on the last line.
We parse that with a tolerant regex — judges occasionally add commentary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..dataset import PromptRow
from ..judge_client import JudgeClient, JudgeVerdict
from . import GradeResult

# The scoring prompt is stable and factored so both judges see the same thing.
# We keep it terse on purpose — long rubric preambles measurably nudge judges
# toward higher scores (see MT-Bench verbosity bias literature).
_RUBRIC_TEMPLATE = """You are grading an AI assistant's answer.

[Prompt the assistant was given]
{prompt}

[Assistant's answer]
{output}

[Grading rubric]
{rubric}

Output format (strict):
- One line of brief reasoning (under 40 words).
- Then a line containing ONLY "SCORE: N" where N is an integer 1-5.
Do not output anything after the SCORE line.
"""

# Score line must be exact form SCORE: N. Tolerate leading whitespace.
_SCORE_RE = re.compile(r"^\s*SCORE:\s*([1-5])\s*$", re.MULTILINE)

# Default pass threshold — a dual-judge "pass" means BOTH >= this value.
# 4 means "meets the bar"; 5 is rare, 3 means "acceptable but degraded".
DEFAULT_PASS_SCORE = 4


@dataclass
class RubricGrader:
    """LLM rubric grader. Pass a list of 1-2 JudgeClient instances.

    With 2 judges, both must score >= pass_score for the output to pass.
    With 1 judge, single-judge mode: >= pass_score passes.
    """

    judges: list[JudgeClient] = field(default_factory=list)
    pass_score: int = DEFAULT_PASS_SCORE
    name: str = "rubric"

    async def grade(self, row: PromptRow, output: str) -> GradeResult:
        if not self.judges:
            return GradeResult(False, 0.0, "no judges configured")
        if not row.rubric:
            return GradeResult(False, 0.0, "rubric grader requires 'rubric' field")

        prompt = _RUBRIC_TEMPLATE.format(prompt=row.prompt, output=output, rubric=row.rubric)

        verdicts: list[JudgeVerdict] = []
        tokens_used = 0
        for judge in self.judges:
            v = await judge.judge(prompt)
            verdicts.append(v)
            tokens_used += v.tokens_used
            if v.score is None:
                return GradeResult(
                    False,
                    0.0,
                    f"judge {judge.label} returned unparseable verdict: {v.raw[:120]!r}",
                    judge_tokens=tokens_used,
                )

        scores = [v.score for v in verdicts if v.score is not None]
        # In dual mode, pass only if BOTH >= threshold.
        # In single mode, identical result.
        min_score = min(scores)
        avg_score = sum(scores) / len(scores)
        passed = all(s >= self.pass_score for s in scores)

        # Normalize to 0-1 for aggregation: treat 1 as 0.0 and 5 as 1.0 linearly.
        normalized = (avg_score - 1) / 4

        detail_parts = [f"{j.label}={s}" for j, s in zip(self.judges, scores, strict=False)]
        detail = " ".join(detail_parts)
        if not passed:
            detail += f" (min={min_score} < pass={self.pass_score})"

        return GradeResult(
            passed=passed,
            score=normalized,
            detail=detail,
            judge_tokens=tokens_used,
        )


def score_from_verdict_text(text: str) -> int | None:
    """Parse 'SCORE: N' from a judge's response text. Exposed for testing."""
    m = _SCORE_RE.search(text)
    if not m:
        return None
    return int(m.group(1))
