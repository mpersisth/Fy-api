"""Shared verdict and report structures for image canary (5A + 5B)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ProbeOutcome:
    probe_id: str
    method: str
    passed: bool
    score: float
    detail: str
    confidence: str = ""

    def to_dict(self) -> dict:
        return {
            "probe_id": self.probe_id,
            "method": self.method,
            "passed": self.passed,
            "score": self.score,
            "detail": self.detail,
            "confidence": self.confidence,
        }


VERDICT_PASS = "PASS"
VERDICT_MISMATCH = "MISMATCH"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


@dataclass
class CanaryReport:
    mode: str
    channel_name: str
    model: str
    generated_at_unix: float = field(default_factory=time.time)
    outcomes: list[ProbeOutcome] = field(default_factory=list)
    combined_verdict: str = ""
    combined_confidence: str = ""
    vendor_compare_summary: str = ""
    substitution_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "channel_name": self.channel_name,
            "model": self.model,
            "generated_at_unix": self.generated_at_unix,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "combined_verdict": self.combined_verdict,
            "combined_confidence": self.combined_confidence,
            "vendor_compare_summary": self.vendor_compare_summary,
            "substitution_summary": self.substitution_summary,
        }
