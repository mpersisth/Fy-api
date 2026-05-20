"""Unified markdown report — sales-friendly, verdict at top."""

from __future__ import annotations

import datetime
from pathlib import Path

from .config import Config
from .orchestrator import EvalResult, ModelResult, TestResult, Verdict


def generate_markdown(cfg: Config, result: EvalResult) -> str:
    lines: list[str] = []
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    lines.append(f"# 渠道评估报告：{result.channel_name}")
    lines.append("")
    lines.append(f"- **测试时间**: {now}")
    lines.append(f"- **渠道 ID**: {cfg.channel.pin_channel_id or 'N/A'}")
    lines.append(f"- **接口地址**: {cfg.channel.base_url}")
    lines.append("")

    # === Verdict (most important, at the very top) ===
    lines.append("---")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    v = result.overall_verdict
    if v == Verdict.PASS:
        lines.append("**PASS** — 该渠道各项测试通过，可以正常使用。")
    elif v == Verdict.CONDITIONAL:
        lines.append("**CONDITIONAL** — 该渠道基本可用，但存在以下风险点需关注：")
    else:
        lines.append("**FAIL** — 该渠道存在严重问题，不建议使用：")
    lines.append("")

    # Collect all risks
    risks = []
    for mr in result.model_results:
        for r in mr.risks:
            risks.append(f"[{mr.model_type}/{mr.model_name}] {r}")
    if risks:
        lines.append("### 风险点")
        lines.append("")
        for risk in risks:
            lines.append(f"- {risk}")
        lines.append("")

    # === Overview table ===
    lines.append("---")
    lines.append("")
    lines.append("## 模型支持概览")
    lines.append("")
    lines.extend(_overview_table(result))
    lines.append("")

    # === Detailed results per model ===
    lines.append("---")
    lines.append("")
    lines.append("## 详细结果")
    lines.append("")
    for mr in result.model_results:
        lines.append(f"### [{mr.model_type}] {mr.model_name} — {mr.verdict.value}")
        lines.append("")
        if mr.results:
            lines.append("| 测试 | 结果 | 说明 |")
            lines.append("|------|------|------|")
            for r in mr.results:
                status = "PASS" if r.passed else "FAIL"
                lines.append(f"| {r.test_name} | {status} | {r.detail[:80]} |")
            # Performance metrics
            for r in mr.results:
                if r.metrics and r.test_name == "load":
                    lines.append("")
                    m = r.metrics
                    lines.append(f"  - 并发: {m.get('concurrency', '-')}, "
                                f"请求数: {m.get('total', '-')}, "
                                f"成功率: {m.get('success_rate', 0):.0%}, "
                                f"P95: {m.get('p95_sec', 0):.1f}s")
        lines.append("")

    return "\n".join(lines)


def _overview_table(result: EvalResult) -> list[str]:
    lines = []
    lines.append("| 类型 | 模型 | 冒烟 | 负载 | 质量 | 安全 | 综合 |")
    lines.append("|------|------|------|------|------|------|------|")
    for mr in result.model_results:
        row = [mr.model_type, mr.model_name]
        test_map = {r.test_name: r.passed for r in mr.results}
        for t in ["smoke", "load", "quality", "safety"]:
            if t in test_map:
                row.append("PASS" if test_map[t] else "FAIL")
            else:
                row.append("-")
        row.append(mr.verdict.value)
        lines.append("| " + " | ".join(row) + " |")
    return lines


def save_report(cfg: Config, result: EvalResult) -> str:
    md = generate_markdown(cfg, result)
    path = Path(cfg.report.output_dir)
    path.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = cfg.channel.name.replace(" ", "_")
    filename = f"eval-{name}-{now}.md"
    filepath = path / filename
    filepath.write_text(md, encoding="utf-8")
    return str(filepath)
