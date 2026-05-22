from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fy_integrity.client import IntegrityClient
    from fy_integrity.config import IntegrityConfig


@dataclass
class ProbeResult:
    probe_name: str
    passed: bool
    severity: str  # "critical" | "warning" | "info"
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)


class BaseProbe(ABC):
    name: str = "unnamed"
    severity: str = "warning"

    @abstractmethod
    async def run(
        self, client: IntegrityClient, config: IntegrityConfig
    ) -> ProbeResult: ...

    def skip_result(self, reason: str) -> ProbeResult:
        return ProbeResult(
            probe_name=self.name,
            passed=True,
            severity="info",
            summary=f"skipped: {reason}",
            details={"skipped": True, "reason": reason},
        )

    def pass_result(self, summary: str, **details: Any) -> ProbeResult:
        return ProbeResult(
            probe_name=self.name,
            passed=True,
            severity="info",
            summary=summary,
            details=details,
        )

    def fail_result(
        self, summary: str, evidence: list[dict] | None = None, **details: Any
    ) -> ProbeResult:
        return ProbeResult(
            probe_name=self.name,
            passed=False,
            severity=self.severity,
            summary=summary,
            details=details,
            evidence=evidence or [],
        )
