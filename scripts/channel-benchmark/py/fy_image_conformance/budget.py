"""Budget tracking and cost estimation for image conformance testing."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepEstimate:
    step_name: str
    estimated_requests: int
    estimated_cost_usd: float
    detail: str = ""


@dataclass
class BudgetTracker:
    max_cost_usd: float | None = None
    warn_cost_usd: float | None = None
    default_cost: float = 0.04
    _spent_usd: float = 0.0
    _step_costs: list[tuple[str, int, float]] = field(default_factory=list)

    def record(self, step_name: str, n_requests: int, cost_per_request: float | None = None) -> None:
        unit = cost_per_request if cost_per_request is not None else self.default_cost
        cost = n_requests * unit
        self._spent_usd += cost
        self._step_costs.append((step_name, n_requests, cost))

    def would_exceed(self, estimated_cost: float) -> bool:
        if self.max_cost_usd is None:
            return False
        return (self._spent_usd + estimated_cost) > self.max_cost_usd

    def should_warn(self) -> bool:
        if self.warn_cost_usd is None:
            return False
        return self._spent_usd > self.warn_cost_usd

    @property
    def total_spent(self) -> float:
        return self._spent_usd

    @property
    def remaining(self) -> float | None:
        if self.max_cost_usd is None:
            return None
        return max(0.0, self.max_cost_usd - self._spent_usd)

    def summary(self) -> str:
        lines = []
        lines.append("| 步骤 | 请求数 | 费用 (USD) |")
        lines.append("|------|:------:|----------:|")
        for name, n, cost in self._step_costs:
            lines.append(f"| {name} | {n} | ${cost:.3f} |")
        lines.append(f"| **合计** | | **${self._spent_usd:.3f}** |")
        if self.max_cost_usd is not None:
            lines.append(f"\n预算上限: ${self.max_cost_usd:.2f} | 剩余: ${self.remaining:.3f}")
        return "\n".join(lines)


def estimate_steps(cfg, *, smoke_only: bool = False, phase_a_only: bool = False) -> list[StepEstimate]:
    """Estimate cost per step based on config.

    Import Config type at call site to avoid circular import.
    """
    n_channels = len(cfg.gateway.channels)
    cost = cfg.budget.default_cost_per_request if hasattr(cfg, "budget") else 0.04
    estimates = []

    n_sizes = len(cfg.model.supported_sizes)
    n_quals = len(cfg.model.supported_qualities)
    n_fmts = len(cfg.model.supported_formats)
    n_compat = 1 + n_sizes + n_quals + n_fmts + (1 if cfg.model.supports_n_gt_1 else 0) + 1

    estimates.append(StepEstimate(
        "API兼容性 (Layer 1)", n_compat * n_channels, n_compat * n_channels * cost,
        f"{n_compat} cases × {n_channels} channels",
    ))
    estimates.append(StepEstimate(
        "输出验证 (Layer 2)", 1 * n_channels, 1 * n_channels * cost,
        f"1 generation × {n_channels} channels",
    ))

    if smoke_only:
        return estimates

    pf = cfg.suites.prompt_follow
    if pf.enabled:
        judge_repeat = getattr(pf, "judge_repeat", 3)
        n_prompts = pf.sample_count
        n_gen = n_prompts * n_channels
        n_judge = n_prompts * n_channels * judge_repeat
        judge_cost = 0.005
        estimates.append(StepEstimate(
            "质量筛选 Phase A (Layer 3)", n_gen,
            n_gen * cost + n_judge * judge_cost,
            f"{n_prompts} prompts × {n_channels} ch + {judge_repeat}× judge",
        ))

    if phase_a_only:
        return estimates

    if cfg.suites.perf.enabled:
        max_reqs = cfg.suites.perf.max_requests_per_channel or 50
        n_perf = max_reqs * n_channels
        estimates.append(StepEstimate(
            "性能测试 (Layer 4)", n_perf, n_perf * cost,
            f"~{max_reqs} reqs × {n_channels} channels",
        ))

    if cfg.suites.safety:
        n_safety = 14 * n_channels
        estimates.append(StepEstimate(
            "安全测试 (Layer 5)", n_safety, n_safety * cost,
            f"14 cases × {n_channels} channels",
        ))

    return estimates
