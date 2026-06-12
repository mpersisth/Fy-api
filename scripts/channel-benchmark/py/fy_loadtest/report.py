"""Report writers: JSON, CSV, markdown summary table, and PDF."""

from __future__ import annotations

import csv
import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

from .metrics import CeilingResult, LevelAggregate
from .runner import MultiChannelResult, RampResult, SuiteResult

_CHANNEL_COLORS = [
    '#3498db', '#e74c3c', '#2ecc71', '#f39c12', '#9b59b6',
    '#1abc9c', '#e67e22', '#34495e', '#16a085', '#c0392b',
]


def write_reports(mc: MultiChannelResult, formats: list[str], out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    written: list[Path] = []
    for fmt in formats:
        if fmt == "json":
            written.append(_write_json(mc, out, ts))
        elif fmt == "csv":
            written.append(_write_csv(mc, out, ts))
        elif fmt == "markdown":
            written.append(_write_md(mc, out, ts))
        elif fmt == "pdf":
            written.append(_write_pdf(mc, out, ts))
        else:
            raise ValueError(f"unknown export format: {fmt}")
    return written


def write_suite_reports(suite: SuiteResult, formats: list[str], out_dir: str | Path) -> list[Path]:
    written: list[Path] = []
    for mc in suite.model_results:
        written.extend(write_reports(mc, formats, out_dir))
    return written


def _ch_label(r: RampResult) -> str:
    if r.channel_name:
        return r.channel_name
    if r.pin_channel_id is not None:
        return f"channel-{r.pin_channel_id}"
    return "default"


def _write_json(mc: MultiChannelResult, out: Path, ts: str) -> Path:
    path = out / f"loadtest_{ts}.json"
    channels = []
    for r in mc.results:
        ch_data: dict = {
            "channel_name": _ch_label(r),
            "pin_channel_id": r.pin_channel_id,
            "levels": [dataclasses.asdict(lv) for lv in r.levels],
        }
        if r.ceiling is not None:
            ch_data["ceiling"] = dataclasses.asdict(r.ceiling)
        channels.append(ch_data)
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gateway": mc.base_url,
        "model": mc.model,
        "channels": channels,
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    return path


_CSV_HEADER = [
    "channel", "concurrency", "total", "ok", "failed", "success_rate_pct",
    "wall_time_s", "rps", "rpm", "aggregate_tok_per_s",
    "input_tpm", "output_tpm", "total_tpm",
    "e2e_p50_ms", "e2e_p95_ms", "e2e_p99_ms",
    "ttft_p50_ms", "ttft_p95_ms", "ttft_p99_ms",
    "itl_p50_ms", "itl_p95_ms",
    "tpot_p50_ms", "tpot_p95_ms",
    "per_req_tok_per_s_avg", "per_req_tok_per_s_p50",
    "avg_prompt_tokens", "avg_completion_tokens", "avg_cached_tokens",
    "errors_429", "errors_5xx", "errors_timeout",
    "error_rate_429_pct", "error_rate_5xx_pct", "error_rate_timeout_pct",
    "goodput_req_per_s", "top_error",
]


def _write_csv(mc: MultiChannelResult, out: Path, ts: str) -> Path:
    path = out / f"loadtest_{ts}.csv"
    with path.open("w", newline="") as f:
        f.write(f"# model={mc.model} gateway={mc.base_url}\n")
        w = csv.writer(f)
        w.writerow(_CSV_HEADER)
        for r in mc.results:
            label = _ch_label(r)
            for lv in r.levels:
                w.writerow([
                    label,
                    lv.concurrency, lv.total, lv.ok, lv.failed, f"{lv.success_rate_pct:.1f}",
                    f"{lv.wall_time_s:.2f}", f"{lv.throughput_req_per_s:.2f}",
                    f"{lv.rpm:.1f}",
                    f"{lv.aggregate_tok_per_s:.1f}",
                    f"{lv.input_tpm:.1f}", f"{lv.output_tpm:.1f}", f"{lv.total_tpm:.1f}",
                    _fmt(lv.e2e.p50_ms), _fmt(lv.e2e.p95_ms), _fmt(lv.e2e.p99_ms),
                    _fmt(lv.ttft.p50_ms), _fmt(lv.ttft.p95_ms), _fmt(lv.ttft.p99_ms),
                    _fmt(lv.itl.p50_ms), _fmt(lv.itl.p95_ms),
                    _fmt(lv.tpot.p50_ms), _fmt(lv.tpot.p95_ms),
                    f"{lv.per_request_tok_per_s.avg:.2f}",
                    f"{lv.per_request_tok_per_s.p50:.2f}",
                    f"{lv.avg_prompt_tokens:.1f}",
                    f"{lv.avg_completion_tokens:.1f}",
                    f"{lv.avg_cached_tokens:.1f}",
                    lv.errors_429, lv.errors_5xx, lv.errors_timeout,
                    f"{lv.error_rate_429_pct:.1f}",
                    f"{lv.error_rate_5xx_pct:.1f}",
                    f"{lv.error_rate_timeout_pct:.1f}",
                    _fmt_opt(lv.goodput_req_per_s),
                    _top_error(lv),
                ])
    return path


def _write_md(mc: MultiChannelResult, out: Path, ts: str) -> Path:
    path = out / f"loadtest_{ts}.md"
    lines: list[str] = []
    lines.append(f"# Load test: {mc.model}")
    lines.append("")
    lines.append(f"- Gateway: `{mc.base_url}`")
    lines.append(f"- Generated: {datetime.now(timezone.utc).isoformat()}")

    for r in mc.results:
        label = _ch_label(r)
        lines.append("")
        lines.append(f"## {label}")
        if r.pin_channel_id is not None:
            lines.append(f"- Channel ID: `{r.pin_channel_id}`")
        lines.append("")
        lines.append("| Concurrency | OK/Total | Succ% | RPM | In TPM | Out TPM | E2E p50/p95 (ms) | TTFT p50/p95 (ms) | TPOT p50 (ms) | 429 | 5xx | Timeout |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for lv in r.levels:
            lines.append(
                "| {c} | {ok}/{tot} | {sr:.1f}% | {rpm:.0f} | {itpm:.0f} | {otpm:.0f} | {e50:.0f}/{e95:.0f} | {t50}/{t95} | {tpot} | {e429} | {e5xx} | {eto} |".format(
                    c=lv.concurrency, ok=lv.ok, tot=lv.total, sr=lv.success_rate_pct,
                    rpm=lv.rpm, itpm=lv.input_tpm, otpm=lv.output_tpm,
                    e50=lv.e2e.p50_ms, e95=lv.e2e.p95_ms,
                    t50=_fmt(lv.ttft.p50_ms) or "-", t95=_fmt(lv.ttft.p95_ms) or "-",
                    tpot=_fmt(lv.tpot.p50_ms) or "-",
                    e429=f"{lv.errors_429}({lv.error_rate_429_pct:.1f}%)" if lv.errors_429 else "0",
                    e5xx=f"{lv.errors_5xx}({lv.error_rate_5xx_pct:.1f}%)" if lv.errors_5xx else "0",
                    eto=f"{lv.errors_timeout}({lv.error_rate_timeout_pct:.1f}%)" if lv.errors_timeout else "0",
                )
            )
        has_errors = any(lv.error_breakdown for lv in r.levels)
        if has_errors:
            lines.append("")
            lines.append(f"### Errors ({label})")
            lines.append("")
            lines.append("| Concurrency | Error signature | Count |")
            lines.append("|---:|---|---:|")
            for lv in r.levels:
                for sig, n in sorted(lv.error_breakdown.items(), key=lambda kv: -kv[1]):
                    trim = sig.replace("|", "\\|")
                    if len(trim) > 120:
                        trim = trim[:117] + "..."
                    lines.append(f"| {lv.concurrency} | `{trim}` | {n} |")

    conclusion = _build_conclusion(mc)
    if conclusion:
        lines.append("")
        lines.append("## 结论")
        lines.append("")
        for cl in conclusion:
            lines.append(cl)

    # Ceiling finder section
    multi = len(mc.results) > 1
    for r in mc.results:
        if r.ceiling is not None:
            label = _ch_label(r)
            lines.append("")
            lines.append(f"## 速率限制天花板{f' ({label})' if multi else ''}")
            lines.append("")
            lines.append("| 指标 | 稳态实测 | Provider 标称 |")
            lines.append("|---|---|---|")
            lines.append(f"| RPM | {r.ceiling.measured_rpm:.0f} | {_fmt_header_limit(r.ceiling.header_rpm_limit)} |")
            lines.append(f"| Input TPM | {r.ceiling.measured_input_tpm:.0f} | — |")
            lines.append(f"| Output TPM | {r.ceiling.measured_output_tpm:.0f} | — |")
            lines.append(f"| Total TPM | {r.ceiling.measured_total_tpm:.0f} | {_fmt_header_limit(r.ceiling.header_tpm_limit)} |")
            lines.append("")
            lines.append(f"- 限制类型: {r.ceiling.limit_type}")
            lines.append(f"- 置信度: {r.ceiling.confidence}")
            lines.append(f"- 探测并发: {r.ceiling.ceiling_concurrency}")
            if r.ceiling.first_429_concurrency is not None:
                lines.append(f"- 首次 429 并发: {r.ceiling.first_429_concurrency}")
            lines.append(f"- 稳态验证: 持续 {r.ceiling.sustain_duration_s:.0f}s, 成功率 {r.ceiling.sustain_success_rate_pct:.1f}%")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _fmt(v: float) -> str:
    return f"{v:.1f}" if v else ""


def _fmt_opt(v: float | None) -> str:
    if v is None:
        return ""
    return f"{v:.2f}"


def _fmt_header_limit(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.0f}"


def _top_error(lv: LevelAggregate) -> str:
    if not lv.error_breakdown:
        return ""
    sig, n = max(lv.error_breakdown.items(), key=lambda kv: kv[1])
    sig_short = sig if len(sig) <= 80 else sig[:77] + "..."
    return f"{sig_short} (x{n})"


def _build_conclusion(mc: MultiChannelResult) -> list[str]:
    multi = len(mc.results) > 1
    lines: list[str] = []

    for r in mc.results:
        label = _ch_label(r)
        if not r.levels:
            continue
        total = sum(lv.total for lv in r.levels)
        ok = sum(lv.ok for lv in r.levels)
        sr = (ok / total * 100) if total > 0 else 0
        max_c = max(lv.concurrency for lv in r.levels)
        max_rps = max(lv.throughput_req_per_s for lv in r.levels)
        max_rps_c = max(r.levels, key=lambda lv: lv.throughput_req_per_s).concurrency
        low_c_lv = min(r.levels, key=lambda lv: lv.concurrency)
        high_c_lv = max(r.levels, key=lambda lv: lv.concurrency)
        has_fail = any(lv.failed > 0 for lv in r.levels)

        hdr = f"【{label}】" if multi else "【总体表现】"
        lines.append(hdr)
        lines.append(f"  成功率: {sr:.1f}% ({ok}/{total}){'，存在失败请求' if has_fail else ''}")
        lines.append(f"  峰值吞吐: {max_rps:.2f} req/s（并发={max_rps_c}）")
        if r.bottleneck_concurrency is not None:
            lines.append(f"  瓶颈并发: {r.bottleneck_concurrency}（auto-ramp 探测）")
        if low_c_lv.per_request_tok_per_s.samples > 0:
            tps_avg = low_c_lv.per_request_tok_per_s.avg
            tps_p50 = low_c_lv.per_request_tok_per_s.p50
            lines.append(f"  生成速度: avg {tps_avg:.1f} tok/s, p50 {tps_p50:.1f} tok/s (C={low_c_lv.concurrency})")
            if tps_p50 > 0 and tps_p50 < 50:
                lines.append(f"  ⚠ 生成速度偏低（p50 < 50 tok/s），建议排查渠道链路延迟")
        if low_c_lv.ttft.p95_ms > 0:
            lines.append(f"  TTFT p95: {low_c_lv.ttft.p95_ms:.0f}ms (C={low_c_lv.concurrency}) → {high_c_lv.ttft.p95_ms:.0f}ms (C={high_c_lv.concurrency})")
        lines.append(f"  E2E p95: {low_c_lv.e2e.p95_ms:.0f}ms (C={low_c_lv.concurrency}) → {high_c_lv.e2e.p95_ms:.0f}ms (C={high_c_lv.concurrency})")

    if multi and len(mc.results) >= 2:
        lines.append("")
        lines.append("【渠道对比】")
        best_rps = max(mc.results, key=lambda r: max((lv.throughput_req_per_s for lv in r.levels), default=0))
        best_rps_val = max(lv.throughput_req_per_s for lv in best_rps.levels)
        lines.append(f"  吞吐量最高: {_ch_label(best_rps)} ({best_rps_val:.2f} req/s)")

        best_ttft = min(
            mc.results,
            key=lambda r: min((lv.ttft.p95_ms for lv in r.levels if lv.ttft.p95_ms > 0), default=float('inf')),
        )
        best_ttft_val = min((lv.ttft.p95_ms for lv in best_ttft.levels if lv.ttft.p95_ms > 0), default=0)
        if best_ttft_val > 0:
            lines.append(f"  TTFT p95 最低: {_ch_label(best_ttft)} ({best_ttft_val:.0f}ms，低并发)")

        tps_by_channel = [
            (r, min(r.levels, key=lambda lv: lv.concurrency).per_request_tok_per_s.p50)
            for r in mc.results
            if r.levels and min(r.levels, key=lambda lv: lv.concurrency).per_request_tok_per_s.samples > 0
        ]
        if tps_by_channel:
            best_tps = max(tps_by_channel, key=lambda x: x[1])
            lines.append(f"  生成速度最快: {_ch_label(best_tps[0])} (p50 {best_tps[1]:.1f} tok/s，低并发)")

        high_c_levels = [max(r.levels, key=lambda lv: lv.concurrency) for r in mc.results if r.levels]
        if high_c_levels:
            best_high_c = min(
                zip(mc.results, high_c_levels),
                key=lambda pair: pair[1].e2e.p95_ms,
            )
            lines.append(
                f"  高并发稳定性最优: {_ch_label(best_high_c[0])}"
                f"（C={best_high_c[1].concurrency} 时 E2E p95={best_high_c[1].e2e.p95_ms:.0f}ms）"
            )

        all_sr = [(r, sum(lv.ok for lv in r.levels) / max(sum(lv.total for lv in r.levels), 1) * 100) for r in mc.results]
        worst = min(all_sr, key=lambda x: x[1])
        if worst[1] < 100:
            lines.append(f"  注意: {_ch_label(worst[0])} 成功率仅 {worst[1]:.1f}%，建议排查")

    return lines


_CJK_FONT_PATHS = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]


def _register_cjk_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for fp in _CJK_FONT_PATHS:
        if Path(fp).exists():
            try:
                pdfmetrics.registerFont(TTFont("CJK", fp))
                return "CJK"
            except Exception:
                continue
    return "Helvetica"


def _write_pdf(mc: MultiChannelResult, out: Path, ts: str) -> Path:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Image,
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as e:
        raise RuntimeError(
            f"PDF export requires reportlab and matplotlib. Install with: pip install reportlab matplotlib\n"
            f"Original error: {e}"
        ) from e

    cjk = _register_cjk_font()
    cjk_bold = cjk

    _mpl_cjk_fonts = [
        "Hiragino Sans GB", "PingFang SC", "STHeiti", "SimHei",
        "WenQuanYi Micro Hei", "Noto Sans CJK SC", "Microsoft YaHei",
    ]
    for _mf in _mpl_cjk_fonts:
        try:
            from matplotlib.font_manager import FontProperties
            if FontProperties(family=_mf).get_name() != _mf:
                continue
            plt.rcParams["font.sans-serif"] = [_mf] + plt.rcParams.get("font.sans-serif", [])
            plt.rcParams["axes.unicode_minus"] = False
            break
        except Exception:
            continue

    path = out / f"loadtest_{ts}.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)
    story = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontName=cjk,
        fontSize=28, textColor=colors.HexColor('#1a1a1a'), spaceAfter=30, alignment=1)
    heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading2'], fontName=cjk,
        fontSize=16, textColor=colors.HexColor('#2c3e50'), spaceAfter=12, spaceBefore=20)
    body_style = ParagraphStyle('CustomBody', parent=styles['Normal'], fontName=cjk,
        fontSize=11, textColor=colors.HexColor('#333333'), spaceAfter=12)

    multi = len(mc.results) > 1

    story.append(Spacer(1, 2*inch))
    story.append(Paragraph("压测报告", title_style))
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph(f"模型: <b>{mc.model}</b>", body_style))
    story.append(Paragraph(f"网关: <b>{mc.base_url}</b>", body_style))
    if multi:
        ch_names = ", ".join(_ch_label(r) for r in mc.results)
        story.append(Paragraph(f"渠道: <b>{ch_names}</b>", body_style))
    elif mc.results and mc.results[0].pin_channel_id is not None:
        story.append(Paragraph(f"渠道 ID: <b>{mc.results[0].pin_channel_id}</b>", body_style))
    story.append(Paragraph(f"生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}", body_style))
    story.append(PageBreak())

    total_requests = sum(lv.total for r in mc.results for lv in r.levels)
    total_ok = sum(lv.ok for r in mc.results for lv in r.levels)
    overall_success = (total_ok / total_requests * 100) if total_requests > 0 else 0

    story.append(Paragraph("概要", heading_style))
    if multi:
        summary_text = (
            f"本次压测针对模型 <b>{mc.model}</b>，对比了 {len(mc.results)} 个渠道。"
            f"共执行 <b>{total_requests}</b> 个请求，整体成功率 <b>{overall_success:.1f}%</b>。"
        )
    else:
        r0 = mc.results[0]
        max_c = max(lv.concurrency for lv in r0.levels) if r0.levels else 0
        max_rps = max(lv.throughput_req_per_s for lv in r0.levels) if r0.levels else 0
        ch_label_text = f"（渠道 {r0.pin_channel_id}）" if r0.pin_channel_id else ""
        summary_text = (
            f"本次压测针对模型 <b>{mc.model}</b>{ch_label_text}，"
            f"设置了 {len(r0.levels)} 个并发级别（1 到 {max_c} 并发用户）。"
            f"共执行 <b>{total_requests}</b> 个请求，"
            f"整体成功率 <b>{overall_success:.1f}%</b>，"
            f"峰值吞吐量达到 <b>{max_rps:.2f} 请求/秒</b>。"
        )
    story.append(Paragraph(summary_text, body_style))
    story.append(Spacer(1, 0.3*inch))

    for r in mc.results:
        label = _ch_label(r)
        story.append(Paragraph(f"渠道: {label}" if multi else "各并发级别性能指标", heading_style))

        table_data = [['并发数', '成功率', 'RPM', 'E2E p95\n(ms)', 'TTFT p95\n(ms)', 'TPOT p50\n(ms)', '429/5xx/TO']]
        for lv in r.levels:
            err_cell = f"{lv.errors_429}/{lv.errors_5xx}/{lv.errors_timeout}"
            table_data.append([
                str(lv.concurrency), f"{lv.success_rate_pct:.1f}%",
                f"{lv.rpm:.0f}",
                _fmt(lv.e2e.p95_ms) or "-", _fmt(lv.ttft.p95_ms) or "-",
                _fmt(lv.tpot.p50_ms) or "-", err_cell,
            ])

        table = Table(table_data, colWidths=[0.9*inch, 0.9*inch, 0.8*inch, 0.9*inch, 0.9*inch, 0.9*inch, 0.8*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), cjk_bold), ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('FONTNAME', (0, 1), (-1, -1), cjk), ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')]),
        ]))
        story.append(table)
        story.append(Spacer(1, 0.3*inch))

    chart_paths: list[Path] = []
    try:
        if multi:
            fig1, ax1 = plt.subplots(figsize=(7, 4))
            for idx, r in enumerate(mc.results):
                label = _ch_label(r)
                c_color = _CHANNEL_COLORS[idx % len(_CHANNEL_COLORS)]
                cs = [lv.concurrency for lv in r.levels]
                rps = [lv.throughput_req_per_s for lv in r.levels]
                ax1.plot(cs, rps, marker='o', linewidth=2, markersize=6, color=c_color, label=label)
            ax1.set_xlabel('并发级别', fontsize=11)
            ax1.set_ylabel('请求/秒', fontsize=11)
            ax1.set_title('吞吐量对比', fontsize=13, fontweight='bold')
            ax1.legend()
            ax1.grid(True, alpha=0.3)
            p1 = out / f"_chart_rps_{ts}.png"
            fig1.tight_layout(); fig1.savefig(p1, dpi=150, bbox_inches='tight'); plt.close(fig1)
            chart_paths.append(p1)

            fig2, ax2 = plt.subplots(figsize=(7, 4))
            for idx, r in enumerate(mc.results):
                label = _ch_label(r)
                c_color = _CHANNEL_COLORS[idx % len(_CHANNEL_COLORS)]
                cs = [lv.concurrency for lv in r.levels]
                e2e = [lv.e2e.p95_ms for lv in r.levels]
                ax2.plot(cs, e2e, marker='s', linewidth=2, markersize=6, color=c_color, label=label)
            ax2.set_xlabel('并发级别', fontsize=11)
            ax2.set_ylabel('E2E p95 延迟 (ms)', fontsize=11)
            ax2.set_title('E2E p95 延迟对比', fontsize=13, fontweight='bold')
            ax2.legend()
            ax2.grid(True, alpha=0.3)
            p2 = out / f"_chart_latency_{ts}.png"
            fig2.tight_layout(); fig2.savefig(p2, dpi=150, bbox_inches='tight'); plt.close(fig2)
            chart_paths.append(p2)

            fig3, ax3 = plt.subplots(figsize=(7, 4))
            for idx, r in enumerate(mc.results):
                label = _ch_label(r)
                c_color = _CHANNEL_COLORS[idx % len(_CHANNEL_COLORS)]
                cs = [lv.concurrency for lv in r.levels]
                ttft = [lv.ttft.p95_ms for lv in r.levels]
                ax3.plot(cs, ttft, marker='^', linewidth=2, markersize=6, color=c_color, label=label)
            ax3.set_xlabel('并发级别', fontsize=11)
            ax3.set_ylabel('TTFT p95 (ms)', fontsize=11)
            ax3.set_title('首 Token 延迟对比', fontsize=13, fontweight='bold')
            ax3.legend()
            ax3.grid(True, alpha=0.3)
            p3 = out / f"_chart_ttft_{ts}.png"
            fig3.tight_layout(); fig3.savefig(p3, dpi=150, bbox_inches='tight'); plt.close(fig3)
            chart_paths.append(p3)
        else:
            r0 = mc.results[0]
            concurrencies = [lv.concurrency for lv in r0.levels]

            fig1, ax1 = plt.subplots(figsize=(7, 4))
            ax1.plot(concurrencies, [lv.throughput_req_per_s for lv in r0.levels],
                     marker='o', linewidth=2, markersize=8, color='#3498db')
            ax1.set_xlabel('并发级别', fontsize=11); ax1.set_ylabel('请求/秒', fontsize=11)
            ax1.set_title('吞吐量 vs 并发数', fontsize=13, fontweight='bold')
            ax1.grid(True, alpha=0.3)
            p1 = out / f"_chart_rps_{ts}.png"
            fig1.tight_layout(); fig1.savefig(p1, dpi=150, bbox_inches='tight'); plt.close(fig1)
            chart_paths.append(p1)

            fig2, ax2 = plt.subplots(figsize=(7, 4))
            ax2.plot(concurrencies, [lv.e2e.p50_ms for lv in r0.levels], marker='o', label='p50', linewidth=2, markersize=6)
            ax2.plot(concurrencies, [lv.e2e.p95_ms for lv in r0.levels], marker='s', label='p95', linewidth=2, markersize=6)
            ax2.plot(concurrencies, [lv.e2e.p99_ms for lv in r0.levels], marker='^', label='p99', linewidth=2, markersize=6)
            ax2.set_xlabel('并发级别', fontsize=11); ax2.set_ylabel('端到端延迟 (ms)', fontsize=11)
            ax2.set_title('E2E 延迟分位数', fontsize=13, fontweight='bold')
            ax2.legend(); ax2.grid(True, alpha=0.3)
            p2 = out / f"_chart_latency_{ts}.png"
            fig2.tight_layout(); fig2.savefig(p2, dpi=150, bbox_inches='tight'); plt.close(fig2)
            chart_paths.append(p2)

            fig3, ax3 = plt.subplots(figsize=(7, 4))
            success_rates = [lv.success_rate_pct for lv in r0.levels]
            colors_bar = ['#27ae60' if sr >= 95 else '#e74c3c' for sr in success_rates]
            ax3.bar(concurrencies, success_rates, color=colors_bar, alpha=0.7, edgecolor='black')
            ax3.set_xlabel('并发级别', fontsize=11); ax3.set_ylabel('成功率 (%)', fontsize=11)
            ax3.set_title('各并发级别成功率', fontsize=13, fontweight='bold')
            ax3.set_ylim(0, 105)
            ax3.axhline(y=95, color='orange', linestyle='--', linewidth=1, label='95% 阈值')
            ax3.legend(); ax3.grid(True, alpha=0.3, axis='y')
            p3 = out / f"_chart_success_{ts}.png"
            fig3.tight_layout(); fig3.savefig(p3, dpi=150, bbox_inches='tight'); plt.close(fig3)
            chart_paths.append(p3)

        story.append(PageBreak())
        story.append(Paragraph("性能图表", heading_style))
        for cp in chart_paths:
            story.append(Image(str(cp), width=6*inch, height=3.5*inch))
            story.append(Spacer(1, 0.3*inch))
    except Exception as e:
        story.append(Paragraph(f"<i>图表生成失败: {e}</i>", body_style))

    story.append(PageBreak())
    story.append(Paragraph("详细指标", heading_style))

    for r in mc.results:
        label = _ch_label(r)
        for lv in r.levels:
            hdr = f"<b>{label} — 并发 {lv.concurrency}</b>" if multi else f"<b>并发级别: {lv.concurrency}</b>"
            story.append(Paragraph(hdr, body_style))
            detail_data = [
                ['指标', '数值'],
                ['总请求数', str(lv.total)],
                ['成功', f"{lv.ok} ({lv.success_rate_pct:.1f}%)"],
                ['失败', str(lv.failed)],
                ['实际耗时', f"{lv.wall_time_s:.2f}s"],
                ['RPM', f"{lv.rpm:.1f}"],
                ['吞吐量', f"{lv.throughput_req_per_s:.2f} req/s"],
                ['Input TPM', f"{lv.input_tpm:.0f}"],
                ['Output TPM', f"{lv.output_tpm:.0f}"],
                ['Total TPM', f"{lv.total_tpm:.0f}"],
                ['Token 吞吐量', f"{lv.aggregate_tok_per_s:.1f} tok/s"],
                ['E2E p50/p95/p99', f"{_fmt(lv.e2e.p50_ms)}/{_fmt(lv.e2e.p95_ms)}/{_fmt(lv.e2e.p99_ms)} ms"],
                ['TTFT p50/p95/p99', f"{_fmt(lv.ttft.p50_ms)}/{_fmt(lv.ttft.p95_ms)}/{_fmt(lv.ttft.p99_ms)} ms"],
                ['TPOT p50/p95', f"{_fmt(lv.tpot.p50_ms)}/{_fmt(lv.tpot.p95_ms)} ms"],
                ['ITL p50/p95', f"{_fmt(lv.itl.p50_ms)}/{_fmt(lv.itl.p95_ms)} ms"],
                ['429 错误', f"{lv.errors_429} ({lv.error_rate_429_pct:.1f}%)"],
                ['5xx 错误', f"{lv.errors_5xx} ({lv.error_rate_5xx_pct:.1f}%)"],
                ['超时', f"{lv.errors_timeout} ({lv.error_rate_timeout_pct:.1f}%)"],
                ['平均 Prompt Tokens', f"{lv.avg_prompt_tokens:.1f}"],
                ['平均 Completion Tokens', f"{lv.avg_completion_tokens:.1f}"],
                ['平均缓存 Tokens', f"{lv.avg_cached_tokens:.1f}"],
            ]
            if lv.goodput_req_per_s is not None:
                detail_data.append(['有效吞吐量 (SLO)', f"{lv.goodput_req_per_s:.2f} req/s"])
            dt = Table(detail_data, colWidths=[2.5*inch, 3*inch])
            dt.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), cjk_bold), ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTNAME', (0, 1), (-1, -1), cjk),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')]),
            ]))
            story.append(dt)
            story.append(Spacer(1, 0.2*inch))

    has_errors = any(lv.error_breakdown for r in mc.results for lv in r.levels)
    if has_errors:
        story.append(PageBreak())
        story.append(Paragraph("错误汇总", heading_style))
        error_data = [['渠道', '并发数', '错误信息', '次数']]
        for r in mc.results:
            label = _ch_label(r)
            for lv in r.levels:
                for sig, n in sorted(lv.error_breakdown.items(), key=lambda kv: -kv[1]):
                    sig_trim = sig if len(sig) <= 60 else sig[:57] + "..."
                    error_data.append([label, str(lv.concurrency), sig_trim, str(n)])
        if len(error_data) > 1:
            et = Table(error_data, colWidths=[1*inch, 0.8*inch, 3.2*inch, 0.8*inch])
            et.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e74c3c')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), cjk_bold), ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('FONTNAME', (0, 1), (-1, -1), cjk),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#ffe6e6')]),
            ]))
            story.append(et)

    conclusion = _build_conclusion(mc)
    if conclusion:
        story.append(PageBreak())
        conclusion_heading = ParagraphStyle('ConclusionHeading', parent=styles['Heading2'], fontName=cjk,
            fontSize=18, textColor=colors.HexColor('#1a6b3f'), spaceAfter=16, spaceBefore=20)
        story.append(Paragraph("结论", conclusion_heading))
        for cl in conclusion:
            if cl.startswith("【"):
                story.append(Spacer(1, 0.15*inch))
                story.append(Paragraph(f"<b>{cl}</b>", body_style))
            elif cl.strip():
                story.append(Paragraph(cl, body_style))

    doc.build(story)
    for cp in chart_paths:
        try:
            cp.unlink()
        except Exception:
            pass
    return path
