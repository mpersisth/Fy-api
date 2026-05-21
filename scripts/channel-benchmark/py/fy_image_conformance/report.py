"""Generate sales-friendly markdown report with verdict at top."""

from __future__ import annotations

import datetime
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


class Verdict:
    PASS = "PASS"
    CONDITIONAL = "CONDITIONAL"
    FAIL = "FAIL"


def generate_markdown(report: FullReport) -> str:
    lines: list[str] = []
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    model = report.config.model.name
    channels = report.config.gateway.channels

    lines.append(f"# 图片渠道测试报告")
    lines.append(f"")
    lines.append(f"- **模型**: {model}")
    lines.append(f"- **测试时间**: {now}")
    ch_names = ", ".join(f"{c.name} (ID:{c.pin_channel_id})" for c in channels)
    lines.append(f"- **渠道**: {ch_names}")
    lines.append("")

    # Verdict section — most important, at the top
    verdict, risks = _compute_verdict(report)
    lines.append("---")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    if verdict == Verdict.PASS:
        lines.append(f"**{verdict}** — 该渠道可以正常使用，各项测试通过。")
    elif verdict == Verdict.CONDITIONAL:
        lines.append(f"**{verdict}** — 该渠道基本可用，但存在以下风险点需关注：")
    else:
        lines.append(f"**{verdict}** — 该渠道不建议使用，存在严重问题：")
    lines.append("")

    if risks:
        lines.append("### 风险点")
        lines.append("")
        for risk in risks:
            lines.append(f"- {risk}")
        lines.append("")

    # Probe results
    if report.probe_results:
        lines.append("---")
        lines.append("")
        lines.append("## 模型支持探测")
        lines.append("")
        for ch_name, probes in report.probe_results.items():
            lines.append(f"### {ch_name}")
            lines.append("")
            supported = [p for p in probes if p.supported]
            unsupported = [p for p in probes if not p.supported]
            if supported:
                lines.append(f"**支持的模型** ({len(supported)}):")
                for p in supported:
                    lines.append(f"- {p.model}")
            if unsupported:
                lines.append(f"\n**不支持** ({len(unsupported)}): "
                           + ", ".join(p.model for p in unsupported))
            lines.append("")

    # API compatibility
    if report.compat_results:
        lines.extend(_section_compat(report.compat_results))

    # Output validation
    if report.output_results:
        lines.extend(_section_output(report.output_results))

    # Prompt adherence
    if report.prompt_results:
        lines.extend(_section_prompt(report.prompt_results))

    # Performance
    if report.perf_results:
        lines.extend(_section_perf(report.perf_results))

    # Safety
    if report.safety_results:
        lines.extend(_section_safety(report.safety_results))

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
    for cr in report.safety_results:
        failed = [c for c in cr.cases if not c.passed]
        for c in failed:
            if c.name in ("nsfw_rejection", "violence_rejection"):
                risks.append(f"[安全] 渠道 {cr.channel.name} 未拦截敏感内容 ({c.name})")

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
        if cr.avg_score < 0.5:
            risks.append(f"渠道 {cr.channel.name} 提示词遵循度低 (均分 {cr.avg_score:.2f})")

    if has_critical:
        return Verdict.FAIL, risks
    elif risks:
        return Verdict.CONDITIONAL, risks
    return Verdict.PASS, []


def _section_compat(results: list[ChannelCompatResult]) -> list[str]:
    lines = ["---", "", "## API 兼容性测试", ""]
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
    lines = ["---", "", "## 输出验证", ""]
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
    lines = ["---", "", "## 提示词遵循度", ""]
    for cr in results:
        lines.append(f"### 渠道: {cr.channel.name} (均分: {cr.avg_score:.2f})")
        lines.append("")
        lines.append("| 测试项 | 得分 | 结果 | 说明 |")
        lines.append("|--------|------|------|------|")
        for r in cr.results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"| {r.prompt_name} | {r.score:.2f} | {status} | {r.reasoning[:60]} |")
        lines.append("")
    return lines


def _section_perf(results: list[PerfStats]) -> list[str]:
    lines = ["---", "", "## 性能测试", ""]
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
    lines = ["---", "", "## 安全与边界测试", ""]
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


def save_report(report: FullReport, output_dir: str) -> str:
    md = generate_markdown(report)
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    model = report.config.model.name.replace("/", "_")
    filename = f"conformance-{model}-{now}.md"
    filepath = path / filename
    filepath.write_text(md, encoding="utf-8")
    return str(filepath)
