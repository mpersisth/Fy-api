"""Report generation for image canary results."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from .verdict import CanaryReport


def generate_markdown(report: CanaryReport) -> str:
    lines: list[str] = []
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    lines.append("# 图片渠道真实性检测报告")
    lines.append("")
    lines.append(f"- **渠道**: {report.channel_name}")
    lines.append(f"- **模型**: {report.model}")
    lines.append(f"- **模式**: {report.mode}")
    lines.append(f"- **检测时间**: {now}")
    lines.append("")

    # Verdict
    lines.append("---")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    verdict_emoji = {"PASS": "✅", "MISMATCH": "🔴", "INCONCLUSIVE": "🟡"}
    emoji = verdict_emoji.get(report.combined_verdict, "❓")
    lines.append(f"**{emoji} {report.combined_verdict}** — "
                f"置信度: {report.combined_confidence}")
    lines.append("")

    passed = sum(1 for o in report.outcomes if o.passed)
    total = len(report.outcomes)
    lines.append(f"探针通过率: {passed}/{total} "
                f"({passed/total:.0%})" if total > 0 else "无探针结果")
    lines.append("")

    # Group outcomes by method
    methods = {}
    for o in report.outcomes:
        methods.setdefault(o.method, []).append(o)

    # 5A section
    _5a_methods = {"clip", "color_histogram", "vlm_comparison",
                   "success_rate", "latency_regression", "generation"}
    has_5a = any(m in _5a_methods for m in methods)
    if has_5a:
        lines.extend(_section_5a(methods))

    # 5B-1 Fingerprint
    if "fingerprint" in methods:
        lines.extend(_section_fingerprint(methods["fingerprint"]))

    # 5B-2 Cross-channel
    if "cross_channel" in methods:
        lines.extend(_section_cross_channel(methods["cross_channel"]))

    # 5B-3 Capability
    if "capability" in methods:
        lines.extend(_section_capability(methods["capability"]))

    return "\n".join(lines)


def _section_5a(methods: dict) -> list[str]:
    lines = ["---", "", "## Step 5A: Vendor 直连对比", ""]
    lines.append("| 探针 | 方法 | 结果 | 得分 | 说明 |")
    lines.append("|------|------|------|:----:|------|")
    for method_name in ("clip", "color_histogram", "vlm_comparison",
                        "success_rate", "latency_regression", "generation"):
        for o in methods.get(method_name, []):
            status = "PASS" if o.passed else "FAIL"
            lines.append(
                f"| {o.probe_id} | {o.method} | {status} "
                f"| {o.score:.3f} | {o.detail[:70]} |"
            )
    lines.append("")
    return lines


def _section_fingerprint(outcomes: list) -> list[str]:
    lines = ["---", "", "## Step 5B-1: 模型指纹检测", ""]
    lines.append("| 探针 | 结果 | 得分 | 置信度 | 说明 |")
    lines.append("|------|------|:----:|:------:|------|")
    for o in outcomes:
        status = "PASS" if o.passed else "FAIL"
        lines.append(
            f"| {o.probe_id} | {status} | {o.score:.1f} "
            f"| {o.confidence or '-'} | {o.detail[:70]} |"
        )
    lines.append("")
    return lines


def _section_cross_channel(outcomes: list) -> list[str]:
    lines = ["---", "", "## Step 5B-2: 跨渠道对比", ""]
    lines.append("| 探针 | 结果 | CLIP余弦 | 说明 |")
    lines.append("|------|------|:--------:|------|")
    for o in outcomes:
        status = "PASS" if o.passed else "FAIL"
        lines.append(
            f"| {o.probe_id} | {status} | {o.score:.4f} | {o.detail[:70]} |"
        )
    lines.append("")
    return lines


def _section_capability(outcomes: list) -> list[str]:
    lines = ["---", "", "## Step 5B-3: 能力边界探针", ""]
    lines.append("| 探针 | 结果 | 得分 | 说明 |")
    lines.append("|------|------|:----:|------|")
    for o in outcomes:
        status = "PASS" if o.passed else "FAIL"
        lines.append(
            f"| {o.probe_id} | {status} | {o.score:.2f} | {o.detail[:70]} |"
        )
    passed = sum(1 for o in outcomes if o.passed)
    total = len(outcomes)
    lines.append(f"\n能力通过率: {passed}/{total}")
    lines.append("")
    return lines


def report_to_dict(report: CanaryReport) -> dict:
    return report.to_dict()


def save_report(report: CanaryReport, output_dir: str) -> tuple[str, str]:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    model = report.model.replace("/", "_")

    json_name = f"canary-image-{model}-{now}.json"
    md_name = f"canary-image-{model}-{now}.md"

    json_path = path / json_name
    md_path = path / md_name

    json_path.write_text(
        json.dumps(report_to_dict(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    md_path.write_text(generate_markdown(report), encoding="utf-8")

    return str(json_path), str(md_path)
