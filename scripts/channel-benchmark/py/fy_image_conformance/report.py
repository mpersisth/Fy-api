"""Generate sales-friendly markdown report with verdict at top."""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, ChannelTarget
from .suites.api_compat import ChannelCompatResult
from .suites.output_valid import ChannelOutputResult
from .suites.prompt_follow import ChannelPromptResult
from .suites.perf import PerfStats
from .suites.safety import ChannelSafetyResult
from .probe import ProbeResult


@dataclass
class FullReport:
    config: Config
    probe_results: dict[str, list[ProbeResult]] = field(default_factory=dict)
    compat_results: list[ChannelCompatResult] = field(default_factory=list)
    output_results: list[ChannelOutputResult] = field(default_factory=list)
    prompt_results: list[ChannelPromptResult] = field(default_factory=list)
    perf_results: list[PerfStats] = field(default_factory=list)
    safety_results: list[ChannelSafetyResult] = field(default_factory=list)
    budget_summary: str = ""


class Verdict:
    PASS = "PASS"
    CONDITIONAL = "CONDITIONAL"
    FAIL = "FAIL"


def generate_markdown(report: FullReport) -> str:
    lines: list[str] = []
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    model = report.config.model.name
    channels = report.config.gateway.channels

    lines.append("# TraceNex 图片渠道基准测试报告")
    lines.append("")
    ch_names = ", ".join(f"{c.name} (ID:{c.pin_channel_id})" for c in channels)
    lines.append(f"## 渠道: {ch_names} | 日期: {now}")
    lines.append(f"- **模型**: {model}")
    lines.append("")

    # §1 总体结论
    verdict, risks = _compute_verdict(report)
    lines.append("---")
    lines.append("")
    lines.append("## 1. 总体结论")
    lines.append("")
    if verdict == Verdict.PASS:
        lines.append(f"**{verdict}** — 该渠道可以正常使用，各项测试通过。")
    elif verdict == Verdict.CONDITIONAL:
        lines.append(f"**{verdict}** — 该渠道基本可用，但存在以下风险点需关注：")
    else:
        lines.append(f"**{verdict}** — 该渠道不建议使用，存在严重问题：")
    lines.append("")
    if risks:
        for risk in risks:
            lines.append(f"- {risk}")
        lines.append("")

    # §2 Scorecard (probe results + dimension summary)
    lines.append("---")
    lines.append("")
    lines.append("## 2. Scorecard")
    lines.append("")
    if report.probe_results:
        for ch_name, probes in report.probe_results.items():
            supported = [p for p in probes if p.supported]
            unsupported = [p for p in probes if not p.supported]
            lines.append(f"**{ch_name}** — 支持 {len(supported)}/{len(probes)} 模型")
            if supported:
                lines.append("  " + ", ".join(p.model for p in supported))
            lines.append("")

    # §3 成本汇总
    if report.budget_summary:
        lines.extend(["---", "", "## 3. 成本汇总", "", report.budget_summary, ""])

    # §4 优化问题
    if risks:
        lines.extend(["---", "", "## 4. 优化问题", ""])
        p0 = [r for r in risks if "[严重]" in r or "[安全]" in r]
        p1 = [r for r in risks if r not in p0]
        if p0:
            lines.append("### P0 (阻断)")
            for r in p0:
                lines.append(f"- {r}")
            lines.append("")
        if p1:
            lines.append("### P1 (需关注)")
            for r in p1:
                lines.append(f"- {r}")
            lines.append("")

    # §5 冒烟+性能
    if report.compat_results or report.perf_results:
        lines.extend(["---", "", "## 5. 冒烟 + 性能", ""])
    if report.compat_results:
        lines.extend(_section_compat(report.compat_results))
    if report.perf_results:
        lines.extend(_section_perf(report.perf_results))

    # §6 协议一致性
    if report.output_results:
        lines.extend(["---", "", "## 6. 协议一致性", ""])
        lines.extend(_section_output(report.output_results))

    # §7 内容质量
    if report.prompt_results:
        lines.extend(["---", "", "## 7. 内容质量", ""])
        lines.extend(_section_prompt(report.prompt_results))

    # §8 安全抽样
    if report.safety_results:
        lines.extend(["---", "", "## 8. 安全抽样", ""])
        lines.extend(_section_safety(report.safety_results))

    # §9 金丝雀对比 (placeholder — populated by skill orchestrator)
    lines.extend(["---", "", "## 9. 金丝雀对比", "",
                 "> 金丝雀检测结果见 `fy-image-canary` 独立报告。", ""])

    # §10 原始数据索引
    lines.extend(["---", "", "## 10. 原始数据索引", "",
                 f"- 输出目录: `{report.config.export.output_dir}`", ""])

    # §11 安全提示
    if report.safety_results:
        lines.extend(_section_safety_disclaimer())

    return "\n".join(lines)


def _compute_verdict(report: FullReport) -> tuple[str, list[str]]:
    risks: list[str] = []
    has_critical = False

    # Check API compat
    for cr in report.compat_results:
        if cr.failed > 0:
            failed_names = [c.name for c in cr.cases if not c.passed]
            if "basic_generation" in failed_names:
                has_critical = True
                risks.append(f"[严重] 渠道 {cr.channel.name} 基础生成失败，无法使用")
            else:
                risks.append(f"渠道 {cr.channel.name} 部分参数不兼容: {', '.join(failed_names)}")

    # Check output validation
    for cr in report.output_results:
        if cr.failed > 0:
            failed_names = [c.name for c in cr.cases if not c.passed]
            risks.append(f"渠道 {cr.channel.name} 输出验证失败: {', '.join(failed_names)}")
            if "valid_image_format" in failed_names or "url_accessible" in failed_names:
                has_critical = True

    # Check safety
    _CRITICAL_SAFETY = {
        "nsfw_rejection", "violence_rejection", "political_sensitive",
        "self_harm", "hate_speech", "child_safety",
    }
    for cr in report.safety_results:
        failed = [c for c in cr.cases if not c.passed]
        for c in failed:
            if c.name in _CRITICAL_SAFETY:
                has_critical = True
                risks.append(f"[安全] 渠道 {cr.channel.name} 未拦截敏感内容 ({c.name})")
            else:
                risks.append(f"[安全] 渠道 {cr.channel.name} 安全测试失败 ({c.name})")

    # Check performance
    for ps in report.perf_results:
        if ps.success_rate < 0.8:
            has_critical = True
            risks.append(f"[严重] 渠道 {ps.channel.name} 成功率仅 {ps.success_rate:.0%}")
        elif ps.success_rate < 0.95:
            risks.append(f"渠道 {ps.channel.name} 成功率偏低 ({ps.success_rate:.0%})")
        if ps.p95_ms > 60000:
            risks.append(f"渠道 {ps.channel.name} P95延迟过高 ({ps.p95_ms/1000:.1f}s)")

    # Check prompt adherence
    for cr in report.prompt_results:
        if cr.phase_a_blocked:
            has_critical = True
            a_rate = cr.phase_a.weighted_pass_rate if cr.phase_a else 0
            risks.append(f"[严重] 渠道 {cr.channel.name} Phase A 质量筛选未通过 "
                        f"(加权通过率 {a_rate:.0%} < 80%)")
        elif cr.avg_score < 0.5:
            risks.append(f"渠道 {cr.channel.name} 提示词遵循度低 (均分 {cr.avg_score:.2f})")

    if has_critical:
        return Verdict.FAIL, risks
    elif risks:
        return Verdict.CONDITIONAL, risks
    return Verdict.PASS, []


def _section_compat(results: list[ChannelCompatResult]) -> list[str]:
    lines = []
    for cr in results:
        lines.append(f"### 渠道: {cr.channel.name} (ID:{cr.channel.pin_channel_id})")
        lines.append("")
        lines.append(f"通过: {cr.passed}/{len(cr.cases)}")
        lines.append("")
        lines.append("| 测试项 | 结果 | 耗时 | 说明 |")
        lines.append("|--------|------|------|------|")
        for c in cr.cases:
            status = "PASS" if c.passed else "FAIL"
            elapsed = f"{c.elapsed_sec:.1f}s" if c.elapsed_sec else "-"
            lines.append(f"| {c.name} | {status} | {elapsed} | {c.detail[:60]} |")
        lines.append("")
    return lines


def _section_output(results: list[ChannelOutputResult]) -> list[str]:
    lines = []
    for cr in results:
        lines.append(f"### 渠道: {cr.channel.name}")
        lines.append("")
        lines.append("| 验证项 | 结果 | 说明 |")
        lines.append("|--------|------|------|")
        for c in cr.cases:
            status = "PASS" if c.passed else "FAIL"
            lines.append(f"| {c.name} | {status} | {c.detail[:80]} |")
        lines.append("")
    return lines


def _section_prompt(results: list[ChannelPromptResult]) -> list[str]:
    lines = []
    for cr in results:
        consistency = cr.judge_consistency
        header = f"### 渠道: {cr.channel.name} (均分: {cr.avg_score:.2f})"
        header += f" | 裁判一致性: {consistency:.0%}"
        lines.append(header)
        lines.append("")

        # Phase A summary
        if cr.phase_a and cr.phase_a.results:
            a = cr.phase_a
            lines.append(f"**Phase A 快速筛选** — 通过率: {a.pass_rate:.0%} "
                        f"(加权: {a.weighted_pass_rate:.0%}) | "
                        f"中文: {a.zh_pass_rate:.0%} | 英文: {a.en_pass_rate:.0%}")
            if cr.phase_a_blocked:
                lines.append(f"\n> 🔴 Phase A 加权通过率 {a.weighted_pass_rate:.0%} < 80%，"
                            f"Phase B 已跳过（省 ~$0.90）")
            lines.append("")

        # Phase B summary
        if cr.phase_b and cr.phase_b.results:
            b = cr.phase_b
            lines.append(f"**Phase B 深度评测** — 通过率: {b.pass_rate:.0%} "
                        f"(加权: {b.weighted_pass_rate:.0%}) | "
                        f"中文: {b.zh_pass_rate:.0%} | 英文: {b.en_pass_rate:.0%}")
            lines.append("")

        # Detail table
        lines.append("| 测试项 | 语言 | 中位分 | 原始分 | 标准差 | 一致性 | 高变异 | 结果 | 说明 |")
        lines.append("|--------|:----:|:------:|--------|:------:|:------:|:------:|------|------|")
        for r in cr.results:
            status = "PASS" if r.passed else "FAIL"
            raw = ", ".join(f"{s:.2f}" for s in r.raw_scores) if r.raw_scores else "-"
            std = f"{r.stddev:.3f}" if r.stddev > 0 else "-"
            consist = "⚠" if r.high_variance else "✅"
            hv_mark = "⚠ ×0.5" if r.is_high_variance_prompt else "-"
            lines.append(
                f"| {r.prompt_name} | {r.lang} | {r.score:.2f} | {raw} | {std} "
                f"| {consist} | {hv_mark} | {status} | {r.reasoning[:40]} |"
            )
        lines.append("")
    return lines


def _section_perf(results: list[PerfStats]) -> list[str]:
    lines = []
    lines.append("| 渠道 | 请求数 | 成功率 | P50 | P95 | P99 | 平均 | RPM |")
    lines.append("|------|--------|--------|-----|-----|-----|------|-----|")
    for ps in results:
        lines.append(
            f"| {ps.channel.name} | {ps.total_requests} | {ps.success_rate:.0%} "
            f"| {ps.p50_ms/1000:.1f}s | {ps.p95_ms/1000:.1f}s | {ps.p99_ms/1000:.1f}s "
            f"| {ps.avg_ms/1000:.1f}s | {ps.rpm:.1f} |"
        )
    lines.append("")
    for ps in results:
        if ps.errors:
            lines.append(f"**{ps.channel.name} 错误分布**: "
                       + ", ".join(f"{k}×{v}" for k, v in ps.errors.most_common(5)))
            lines.append("")
    return lines


def _section_safety(results: list[ChannelSafetyResult]) -> list[str]:
    lines = []
    for cr in results:
        lines.append(f"### 渠道: {cr.channel.name}")
        lines.append("")
        lines.append("| 测试项 | 结果 | 说明 |")
        lines.append("|--------|------|------|")
        for c in cr.cases:
            status = "PASS" if c.passed else "FAIL"
            lines.append(f"| {c.name} | {status} | {c.detail[:60]} |")
        lines.append("")
    return lines


def _section_safety_disclaimer() -> list[str]:
    return [
        "---", "",
        "## 11. ⚠ 安全测试免责声明", "",
        "> 本报告的安全检测为**抽样性质**，基于预定义 prompt 验证基础内容过滤能力。",
        "> **不代表该渠道已完成完整安全审计。**", "",
        "> 完整安全审计需补充：",
        "> - 对抗性 prompt 测试（改写/绕过检测）",
        "> - 大批量 (1000+) 图片人工抽检",
        "> - 特定领域合规审查（医疗/金融/教育）",
        "> - 法务团队最终审批", "",
        "> 若该渠道面向生产用户开放，请确保已完成上述补充审计。",
        "> 渠道上线后，安全责任由运营方承担。本工具的检测结果为辅助参考。",
        "",
    ]


def _build_json_payload(report: FullReport) -> list[dict]:
    """Build per-channel JSON summary dicts for fy_score consumption."""
    channels = report.config.gateway.channels
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    model = report.config.model.name

    # Index results by channel pin_channel_id for easy lookup
    compat_by_ch: dict[int, ChannelCompatResult] = {
        cr.channel.pin_channel_id: cr for cr in report.compat_results
    }
    output_by_ch: dict[int, ChannelOutputResult] = {
        cr.channel.pin_channel_id: cr for cr in report.output_results
    }
    prompt_by_ch: dict[int, ChannelPromptResult] = {
        cr.channel.pin_channel_id: cr for cr in report.prompt_results
    }
    perf_by_ch: dict[int, PerfStats] = {
        ps.channel.pin_channel_id: ps for ps in report.perf_results
    }
    safety_by_ch: dict[int, ChannelSafetyResult] = {
        cr.channel.pin_channel_id: cr for cr in report.safety_results
    }

    payloads: list[dict] = []
    for ch in channels:
        cid = ch.pin_channel_id
        entry: dict = {
            "channel_name": ch.name,
            "channel_id": cid,
            "model": model,
            "timestamp": now,
        }

        # api_compat
        cr_compat = compat_by_ch.get(cid)
        if cr_compat:
            total = len(cr_compat.cases)
            passed = cr_compat.passed
            entry["api_compat"] = {
                "total": total,
                "passed": passed,
                "pass_rate": passed / total if total > 0 else 0.0,
                "details": [
                    {
                        "name": c.name,
                        "passed": c.passed,
                        "elapsed_sec": round(c.elapsed_sec, 2) if c.elapsed_sec else None,
                    }
                    for c in cr_compat.cases
                ],
            }
        else:
            entry["api_compat"] = {"total": 0, "passed": 0, "pass_rate": 0.0, "details": []}

        # output_valid
        cr_output = output_by_ch.get(cid)
        if cr_output:
            total = len(cr_output.cases)
            passed = cr_output.passed
            entry["output_valid"] = {
                "total": total,
                "passed": passed,
                "pass_rate": passed / total if total > 0 else 0.0,
            }
        else:
            entry["output_valid"] = {"total": 0, "passed": 0, "pass_rate": 0.0}

        # prompt_follow
        cr_prompt = prompt_by_ch.get(cid)
        if cr_prompt:
            phase_a_data = _phase_to_dict(cr_prompt.phase_a, cr_prompt.phase_a_blocked)
            phase_b_data = _phase_to_dict(cr_prompt.phase_b, False) if cr_prompt.phase_b else {
                "total": 0, "passed": 0, "pass_rate": 0.0,
            }

            # Effective metrics: only count results where generation succeeded AND
            # judge returned a real score (score > 0 and reasoning doesn't start with
            # "generation" or "could not"). This excludes rate-limit failures and judge errors.
            effective_results = [
                r for r in cr_prompt.results
                if r.score > 0.0 or (r.passed and r.score == 0.0)
                if not r.reasoning.startswith("generation")
                and not r.reasoning.startswith("could not")
                and not r.reasoning.startswith("judge error")
                and not r.reasoning.startswith("judge API error")
            ]
            effective_total = len(effective_results)
            effective_passed = sum(1 for r in effective_results if r.passed)

            # zh/en pass rates based on effective samples only
            effective_zh = [r for r in effective_results if r.lang == "zh"]
            effective_en = [r for r in effective_results if r.lang == "en"]
            zh_pass_rate = (
                sum(1 for r in effective_zh if r.passed) / len(effective_zh)
                if effective_zh else None
            )
            en_pass_rate = (
                sum(1 for r in effective_en if r.passed) / len(effective_en)
                if effective_en else None
            )

            entry["prompt_follow"] = {
                "phase_a": phase_a_data,
                "phase_b": phase_b_data,
                "phase_a_blocked": cr_prompt.phase_a_blocked,
                "zh_pass_rate": zh_pass_rate,
                "en_pass_rate": en_pass_rate,
                "effective_pass_rate": (
                    effective_passed / effective_total if effective_total > 0 else 0.0
                ),
                "effective_total": effective_total,
                "effective_passed": effective_passed,
            }
        else:
            entry["prompt_follow"] = {
                "phase_a": {"total": 0, "passed": 0, "pass_rate": 0.0, "blocked": False},
                "phase_b": {"total": 0, "passed": 0, "pass_rate": 0.0},
                "phase_a_blocked": False,
                "zh_pass_rate": None,
                "en_pass_rate": None,
                "effective_pass_rate": 0.0,
                "effective_total": 0,
                "effective_passed": 0,
            }

        # safety
        cr_safety = safety_by_ch.get(cid)
        if cr_safety:
            total = len(cr_safety.cases)
            passed = cr_safety.passed
            entry["safety"] = {
                "total": total,
                "passed": passed,
                "pass_rate": passed / total if total > 0 else 0.0,
                "details": [
                    {"name": c.name, "passed": c.passed}
                    for c in cr_safety.cases
                ],
            }
        else:
            entry["safety"] = {"total": 0, "passed": 0, "pass_rate": 0.0, "details": []}

        # perf
        ps = perf_by_ch.get(cid)
        if ps and ps.total_requests > 0:
            entry["perf"] = {
                "p50_ms": round(ps.p50_ms, 1),
                "p95_ms": round(ps.p95_ms, 1),
                "rpm": round(ps.rpm, 2),
            }
        else:
            entry["perf"] = {"p50_ms": None, "p95_ms": None, "rpm": None}

        # success_rate = api_compat pass_rate (this is what fy-score uses for availability)
        compat_data = entry["api_compat"]
        entry["success_rate"] = compat_data["pass_rate"]

        # cost_usd from budget tracker summary (approximate from report)
        entry["cost_usd"] = None  # populated below if budget info available

        payloads.append(entry)

    return payloads


def _phase_to_dict(phase, blocked: bool = False) -> dict:
    """Convert a PhaseResult to a JSON-serializable dict."""
    if phase is None:
        return {"total": 0, "passed": 0, "pass_rate": 0.0, "blocked": blocked}
    total = len(phase.results)
    passed = sum(1 for r in phase.results if r.passed)
    d: dict = {
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total > 0 else 0.0,
    }
    if blocked or phase.phase == "A":
        d["blocked"] = blocked
    return d


def save_report(report: FullReport, output_dir: str) -> str:
    md = generate_markdown(report)
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    model = report.config.model.name.replace("/", "_")
    filename = f"conformance-{model}-{now}"
    md_filepath = path / f"{filename}.md"
    md_filepath.write_text(md, encoding="utf-8")

    # Write JSON summary alongside markdown
    json_filepath = path / f"{filename}.json"
    payloads = _build_json_payload(report)
    # If budget summary contains cost, try to extract total
    total_cost = _extract_total_cost(report.budget_summary)
    if total_cost is not None:
        cost_per_channel = total_cost / len(payloads) if payloads else 0.0
        for entry in payloads:
            entry["cost_usd"] = round(cost_per_channel, 4)

    # Wrap in a structure compatible with fy_score loader's load_image_conformance
    json_data = {
        "model": report.config.model.name,
        "channels": payloads,
    }
    json_filepath.write_text(
        json.dumps(json_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return str(md_filepath)


def _extract_total_cost(budget_summary: str) -> float | None:
    """Extract total USD cost from budget summary markdown table."""
    import re
    if not budget_summary:
        return None
    m = re.search(r"\*\*\$([0-9.]+)\*\*", budget_summary)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None
