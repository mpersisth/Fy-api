"""Grader interface and result types.

A grader takes a PromptRow + the channel's actual output and returns a
GradeResult. Graders are pure except for `RubricGrader` and
`SimilarityGrader`, which make their own LLM / embedding calls and therefore
accept a judge client at construction time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..dataset import PromptRow


@dataclass
class GradeResult:
    """Outcome of grading one (prompt, output) pair.

    `score` is always 0.0–1.0 so different graders are averageable; for
    pass/fail graders 0.0 = fail, 1.0 = pass.  `detail` is a short
    human-readable string explaining what the grader saw.
    """

    passed: bool
    score: float
    detail: str = ""
    judge_tokens: int = 0  # for cost accounting of rubric / similarity graders


class Grader(Protocol):
    name: str

    async def grade(self, row: PromptRow, output: str) -> GradeResult: ...
