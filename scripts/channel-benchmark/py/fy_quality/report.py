"""Report writers for quality runs.

We produce:
  - JSON (full per-prompt results, programmatic)
  - CSV (one row per (channel, prompt), spreadsheet-friendly)
  - Markdown summary (per-channel scorecard + per-category breakdown)
  - PDF (professional report with charts and detailed analysis)
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .runner import PromptResult, QualityReport, report_to_dict


def write_reports(r: QualityReport, formats: list[str], out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    written: list[Path] = []
    for fmt in formats:
        if fmt == "json":
            written.append(_json(r, out, ts))
        elif fmt == "csv":
            written.append(_csv(r, out, ts))
        elif fmt == "markdown":
            written.append(_md(r, out, ts))
        elif fmt == "pdf":
            written.append(_pdf(r, out, ts))
        else:
            raise ValueError(f"unknown export format: {fmt!r}")
    return written


def _json(r: QualityReport, out: Path, ts: str) -> Path:
    p = out / f"quality_{ts}.json"
    p.write_text(json.dumps(report_to_dict(r), indent=2, ensure_ascii=False), encoding="utf-8")
    return p


_CSV_HEADER = [
    "channel", "model", "prompt_id", "category", "grader",
    "passed", "score", "detail", "output_tokens", "prompt_tokens",
    "judge_tokens", "elapsed_s", "cached", "error",
]


def _csv(r: QualityReport, out: Path, ts: str) -> Path:
    p = out / f"quality_{ts}.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_CSV_HEADER)
        for pr in r.per_prompt:
            w.writerow([
                pr.channel, pr.model, pr.prompt_id, pr.category, pr.grader,
                "1" if pr.passed else "0",
                f"{pr.score:.3f}",
                pr.detail,
                pr.output_tokens, pr.prompt_tokens, pr.judge_tokens,
                f"{pr.elapsed_s:.2f}", "1" if pr.cached else "0", pr.error,
            ])
    return p


def _md(r: QualityReport, out: Path, ts: str) -> Path:
    p = out / f"quality_{ts}.md"
    lines: list[str] = []
    lines.append("# Quality scorecard")
    lines.append("")
    lines.append(f"- Generated: {datetime.fromtimestamp(r.generated_at_unix, timezone.utc).isoformat()}")
    lines.append(f"- Dataset: `{r.dataset_path}`")
    lines.append(f"- Channels: {', '.join(r.channels)}")
    lines.append("")

    # Overall per-channel pass rate.
    per_channel: dict[str, list[PromptResult]] = defaultdict(list)
    for pr in r.per_prompt:
        per_channel[pr.channel].append(pr)

    lines.append("## Overall")
    lines.append("")
    lines.append("| Channel | Pass | Total | Pass Rate | Avg Score | Judge Tokens |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for ch, rows in per_channel.items():
        ok = sum(1 for x in rows if x.passed)
        total = len(rows)
        avg_score = sum(x.score for x in rows) / total if total else 0.0
        judge_tok = sum(x.judge_tokens for x in rows)
        rate = 100.0 * ok / total if total else 0.0
        lines.append(
            f"| {ch} | {ok} | {total} | {rate:.1f}% | {avg_score:.3f} | {judge_tok} |"
        )

    # Per-category breakdown.
    lines.append("")
    lines.append("## Per-category pass rate")
    lines.append("")
    categories: list[str] = sorted({pr.category for pr in r.per_prompt})
    header = "| Channel | " + " | ".join(categories) + " |"
    sep = "|---|" + "---:|" * len(categories)
    lines.append(header)
    lines.append(sep)
    for ch, rows in per_channel.items():
        by_cat: dict[str, list[PromptResult]] = defaultdict(list)
        for pr in rows:
            by_cat[pr.category].append(pr)
        cells: list[str] = [ch]
        for cat in categories:
            cat_rows = by_cat.get(cat, [])
            if not cat_rows:
                cells.append("—")
            else:
                ok = sum(1 for x in cat_rows if x.passed)
                cells.append(f"{ok}/{len(cat_rows)}")
        lines.append("| " + " | ".join(cells) + " |")

    # Failing prompts table — most useful signal for a regression report.
    failed: list[PromptResult] = [pr for pr in r.per_prompt if not pr.passed]
    if failed:
        lines.append("")
        lines.append("## Failures")
        lines.append("")
        lines.append("| Channel | Prompt | Grader | Detail |")
        lines.append("|---|---|---|---|")
        for pr in failed:
            detail = pr.detail.replace("|", "\\|")
            if len(detail) > 120:
                detail = detail[:117] + "..."
            lines.append(f"| {pr.channel} | `{pr.prompt_id}` | {pr.grader} | {detail} |")

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _pdf(r: QualityReport, out: Path, ts: str) -> Path:
    """Generate a professional PDF quality report with charts and detailed analysis."""
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

    path = out / f"quality_{ts}.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=letter, topMargin=0.75*inch, bottomMargin=0.75*inch)
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=28,
        textColor=colors.HexColor('#1a1a1a'),
        spaceAfter=30,
        alignment=1,
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=16,
        textColor=colors.HexColor('#2c3e50'),
        spaceAfter=12,
        spaceBefore=20,
    )
    
    body_style = ParagraphStyle(
        'CustomBody',
        parent=styles['Normal'],
        fontSize=11,
        textColor=colors.HexColor('#333333'),
        spaceAfter=12,
    )

    # Cover page
    story.append(Spacer(1, 2*inch))
    story.append(Paragraph("Quality Scorecard Report", title_style))
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph(f"Dataset: <b>{r.dataset_path}</b>", body_style))
    story.append(Paragraph(f"Channels: <b>{', '.join(r.channels)}</b>", body_style))
    story.append(Paragraph(f"Generated: {datetime.fromtimestamp(r.generated_at_unix, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}", body_style))
    story.append(PageBreak())

    # Executive Summary
    story.append(Paragraph("Executive Summary", heading_style))
    
    per_channel: dict[str, list[PromptResult]] = defaultdict(list)
    for pr in r.per_prompt:
        per_channel[pr.channel].append(pr)
    
    total_prompts = len(r.per_prompt)
    total_passed = sum(1 for pr in r.per_prompt if pr.passed)
    overall_pass_rate = (total_passed / total_prompts * 100) if total_prompts > 0 else 0
    
    summary_text = f"""
    This quality evaluation tested <b>{len(r.channels)}</b> channel(s) across <b>{total_prompts}</b> prompts 
    from the dataset <i>{r.dataset_path}</i>. The overall pass rate was <b>{overall_pass_rate:.1f}%</b> 
    ({total_passed}/{total_prompts} prompts passed).
    """
    story.append(Paragraph(summary_text, body_style))
    story.append(Spacer(1, 0.3*inch))

    # Overall Results Table
    story.append(Paragraph("Overall Results by Channel", heading_style))
    
    table_data = [['Channel', 'Pass', 'Total', 'Pass Rate', 'Avg Score', 'Judge Tokens']]
    
    for ch, rows in per_channel.items():
        ok = sum(1 for x in rows if x.passed)
        total = len(rows)
        avg_score = sum(x.score for x in rows) / total if total else 0.0
        judge_tok = sum(x.judge_tokens for x in rows)
        rate = 100.0 * ok / total if total else 0.0
        table_data.append([
            ch,
            str(ok),
            str(total),
            f"{rate:.1f}%",
            f"{avg_score:.3f}",
            str(judge_tok),
        ])
    
    table = Table(table_data, colWidths=[1.5*inch, 0.7*inch, 0.7*inch, 1*inch, 1*inch, 1.2*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f5f5')]),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.4*inch))

    # Generate charts
    chart_paths = []
    try:
        # Chart 1: Pass Rate by Channel
        if len(per_channel) > 0:
            fig1, ax1 = plt.subplots(figsize=(7, 4))
            channels = list(per_channel.keys())
            pass_rates = []
            for ch in channels:
                rows = per_channel[ch]
                ok = sum(1 for x in rows if x.passed)
                rate = 100.0 * ok / len(rows) if rows else 0.0
                pass_rates.append(rate)
            
            colors_bar = ['#27ae60' if pr >= 80 else '#e74c3c' for pr in pass_rates]
            ax1.bar(channels, pass_rates, color=colors_bar, alpha=0.7, edgecolor='black')
            ax1.set_xlabel('Channel', fontsize=11)
            ax1.set_ylabel('Pass Rate (%)', fontsize=11)
            ax1.set_title('Pass Rate by Channel', fontsize=13, fontweight='bold')
            ax1.set_ylim(0, 105)
            ax1.axhline(y=80, color='orange', linestyle='--', linewidth=1, label='80% threshold')
            ax1.legend()
            ax1.grid(True, alpha=0.3, axis='y')
            chart1_path = out / f"_chart_passrate_{ts}.png"
            fig1.tight_layout()
            fig1.savefig(chart1_path, dpi=150, bbox_inches='tight')
            plt.close(fig1)
            chart_paths.append(chart1_path)

        # Chart 2: Pass Rate by Category
        categories = sorted({pr.category for pr in r.per_prompt})
        if len(categories) > 0:
            fig2, ax2 = plt.subplots(figsize=(7, 4))
            category_pass_rates = []
            for cat in categories:
                cat_prompts = [pr for pr in r.per_prompt if pr.category == cat]
                ok = sum(1 for pr in cat_prompts if pr.passed)
                rate = 100.0 * ok / len(cat_prompts) if cat_prompts else 0.0
                category_pass_rates.append(rate)
            
            ax2.barh(categories, category_pass_rates, color='#3498db', alpha=0.7, edgecolor='black')
            ax2.set_xlabel('Pass Rate (%)', fontsize=11)
            ax2.set_ylabel('Category', fontsize=11)
            ax2.set_title('Pass Rate by Category', fontsize=13, fontweight='bold')
            ax2.set_xlim(0, 105)
            ax2.axvline(x=80, color='orange', linestyle='--', linewidth=1, label='80% threshold')
            ax2.legend()
            ax2.grid(True, alpha=0.3, axis='x')
            chart2_path = out / f"_chart_category_{ts}.png"
            fig2.tight_layout()
            fig2.savefig(chart2_path, dpi=150, bbox_inches='tight')
            plt.close(fig2)
            chart_paths.append(chart2_path)

        # Add charts to PDF
        if chart_paths:
            story.append(PageBreak())
            story.append(Paragraph("Performance Charts", heading_style))
            
            for chart_path in chart_paths:
                img = Image(str(chart_path), width=6*inch, height=3.5*inch)
                story.append(img)
                story.append(Spacer(1, 0.3*inch))

    except Exception as e:
        story.append(Paragraph(f"<i>Chart generation failed: {e}</i>", body_style))

    # Per-Category Breakdown
    story.append(PageBreak())
    story.append(Paragraph("Per-Category Pass Rate", heading_style))
    
    cat_table_data = [['Channel'] + categories]
    for ch, rows in per_channel.items():
        by_cat: dict[str, list[PromptResult]] = defaultdict(list)
        for pr in rows:
            by_cat[pr.category].append(pr)
        row = [ch]
        for cat in categories:
            cat_rows = by_cat.get(cat, [])
            if not cat_rows:
                row.append("—")
            else:
                ok = sum(1 for x in cat_rows if x.passed)
                row.append(f"{ok}/{len(cat_rows)}")
        cat_table_data.append(row)
    
    cat_table = Table(cat_table_data)
    cat_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')]),
    ]))
    story.append(cat_table)
    story.append(Spacer(1, 0.4*inch))

    # Failures Detail
    failed = [pr for pr in r.per_prompt if not pr.passed]
    if failed:
        story.append(PageBreak())
        story.append(Paragraph("Failure Details", heading_style))
        
        fail_data = [['Channel', 'Prompt ID', 'Category', 'Grader', 'Detail']]
        for pr in failed:
            detail = pr.detail if len(pr.detail) <= 60 else pr.detail[:57] + "..."
            fail_data.append([
                pr.channel,
                pr.prompt_id,
                pr.category,
                pr.grader,
                detail,
            ])
        
        fail_table = Table(fail_data, colWidths=[1*inch, 1.2*inch, 0.9*inch, 0.9*inch, 2.2*inch])
        fail_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e74c3c')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#ffe6e6')]),
        ]))
        story.append(fail_table)

    # Build PDF
    doc.build(story)
    
    # Clean up temporary chart files
    for chart_path in chart_paths:
        try:
            chart_path.unlink()
        except Exception:
            pass
    
    return path
