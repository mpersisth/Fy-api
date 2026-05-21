"""
泰际渠道质量评估报告生成器
只包含泰际 (1ApiKey-Claude) 的测试数据，不暴露任何上游供应商信息。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image, PageBreak,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

_CJK_PATHS = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]
_FONT = "Helvetica"
for _p in _CJK_PATHS:
    if Path(_p).exists():
        try:
            pdfmetrics.registerFont(TTFont("CJK", _p, subfontIndex=0))
            _FONT = "CJK"
            fm.fontManager.addfont(_p)
            mp = fm.FontProperties(fname=_p)
            matplotlib.rcParams["font.sans-serif"] = [mp.get_name()] + matplotlib.rcParams.get("font.sans-serif", [])
            matplotlib.rcParams["font.family"] = "sans-serif"
            matplotlib.rcParams["axes.unicode_minus"] = False
        except Exception:
            continue
        break

HEADER_BG = colors.HexColor("#1E3A5F")
ALT_ROW = colors.HexColor("#F0F4FF")
ACCENT = "#2563EB"
WARN_COLOR = "#DC2626"
MODELS = ["claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6", "claude-opus-4-7"]
MODEL_SHORT = {
    "claude-haiku-4-5-20251001": "Haiku 4.5",
    "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-opus-4-6": "Opus 4.6",
    "claude-opus-4-7": "Opus 4.7",
}

BASE = Path(__file__).parent
GO_JSON = Path("/Users/jimmy/go/src/Fy-api/scripts/channel-benchmark/go/benchmark-results/benchmark_2026-05-15_00-20-31.json")
LT_FILES = {
    "claude-haiku-4-5-20251001": BASE / "loadtest-results/loadtest_combined_claude-haiku-4-5-20251001.json",
    "claude-sonnet-4-6":          BASE / "loadtest-results/loadtest_combined_claude-sonnet-4-6.json",
    "claude-opus-4-6":            BASE / "loadtest-results/loadtest_combined_claude-opus-4-6.json",
    "claude-opus-4-7":            BASE / "loadtest-results/loadtest_combined_claude-opus-4-7.json",
}
QA_JSON = BASE / "quality-results/quality_2026-05-14_16-42-55.json"
CF_FILES = {
    "claude-haiku-4-5-20251001": BASE / "conformance-results/conformance-claude-haiku-4-5-20251001-20260514T164541Z.summary.json",
    "claude-sonnet-4-6":          BASE / "conformance-results/conformance-claude-sonnet-4-6-20260514T164746Z.summary.json",
    "claude-opus-4-6":            BASE / "conformance-results/conformance-claude-opus-4-6-20260514T165001Z.summary.json",
    "claude-opus-4-7":            BASE / "conformance-results/conformance-claude-opus-4-7-20260514T165448Z.summary.json",
}
OUT_PDF = BASE / f"reports/taiji-report-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.pdf"


def _load(p: Path):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _styles():
    s = getSampleStyleSheet()
    def _add(name, parent, **kw):
        if name in s:
            s[name].fontName = _FONT
            for k, v in kw.items():
                setattr(s[name], k, v)
        else:
            s.add(ParagraphStyle(name, parent=s[parent], fontName=_FONT, **kw))
    _add("H1", "Heading1", fontSize=20, textColor=colors.HexColor("#1E3A5F"), spaceAfter=6)
    _add("H2", "Heading2", fontSize=14, textColor=colors.HexColor("#1E3A5F"), spaceAfter=4)
    _add("H3", "Heading3", fontSize=11, textColor=colors.HexColor("#374151"), spaceAfter=3)
    _add("Body", "Normal", fontSize=9, leading=13)
    _add("Small", "Normal", fontSize=8, leading=11, textColor=colors.HexColor("#6B7280"))
    _add("Bullet", "Normal", fontSize=9, leading=13, leftIndent=12, bulletIndent=0)
    _add("WarnBullet", "Normal", fontSize=9, leading=13, leftIndent=12, bulletIndent=0,
         textColor=colors.HexColor(WARN_COLOR))
    for sname in s.byName:
        if hasattr(s[sname], 'fontName') and s[sname].fontName in ('Helvetica', 'Times-Roman'):
            s[sname].fontName = _FONT
    return s


def _tbl_style(header_rows=1):
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, header_rows - 1), HEADER_BG),
        ("TEXTCOLOR",  (0, 0), (-1, header_rows - 1), colors.white),
        ("FONTNAME",   (0, 0), (-1, -1), _FONT),
        ("FONTSIZE",   (0, 0), (-1, 0), 9),
        ("FONTSIZE",   (0, 1), (-1, -1), 8),
        ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ("ALIGN",      (0, 0), (0, -1), "LEFT"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, header_rows), (-1, -1), [ALT_ROW, colors.white]),
        ("GRID",       (0, 0), (-1, -1), 0.4, colors.HexColor("#D1D5DB")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ])


def _p(text, style, s):
    return Paragraph(text, s[style])


def _save_fig(fig, name):
    p = Path(f"/tmp/{name}.png")
    fig.savefig(p, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return p


# ── PLACEHOLDER_SECTIONS ──


def _cover(s):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return [
        Spacer(1, 2*cm),
        _p("泰际 Claude 渠道质量评估报告", "H1", s),
        HRFlowable(width="100%", thickness=2, color=colors.HexColor(ACCENT)),
        Spacer(1, 0.3*cm),
        _p("覆盖 4 个 Claude 模型的全面性能、质量与协议合规测试", "Body", s),
        Spacer(1, 0.3*cm),
        _p(f"测试时间：2026-05-14 ~ 2026-05-15", "Small", s),
        _p(f"报告生成：{now}", "Small", s),
        Spacer(1, 0.5*cm),
        _p("测试模型", "H3", s),
        Table([
            ["模型", "标识"],
            ["Claude Haiku 4.5", "claude-haiku-4-5-20251001"],
            ["Claude Sonnet 4.6", "claude-sonnet-4-6"],
            ["Claude Opus 4.6", "claude-opus-4-6"],
            ["Claude Opus 4.7", "claude-opus-4-7"],
        ], colWidths=[5*cm, 8*cm], style=_tbl_style()),
        Spacer(1, 0.4*cm),
        _p("测试维度", "H3", s),
        Table([
            ["测试项", "说明", "规模"],
            ["连通性 Smoke 测试", "基础延迟 (E2E/TTFT/ITL)，stream + non-stream", "4 模型 × 2 模式 × 3 次 = 24 请求"],
            ["并发负载测试", "从 1 到 2000 并发逐级加压，测吞吐极限", "4 模型 × 13 级 × 20 请求 = 1040 请求"],
            ["质量评测", "7 类评分器 + 双 Judge 评分", "4 模型 × 15 题 = 60 评分"],
            ["协议合规测试", "224 项 OpenAI/Anthropic 协议合规用例", "4 模型 × 224 = 896 请求"],
        ], colWidths=[4*cm, 8*cm, 5.5*cm], style=_tbl_style()),
        PageBreak(),
    ]


def _parse_lt_ch30(data):
    for ch in data.get("channels", []):
        if ch.get("pin_channel_id") == 30:
            levels = ch.get("levels", [])
            if not levels:
                return None
            peak = max(levels, key=lambda l: l.get("throughput_req_per_s", 0))
            return {
                "total_ok": sum(l.get("ok", 0) for l in levels),
                "total_req": sum(l.get("total", 0) for l in levels),
                "peak_rps": peak.get("throughput_req_per_s", 0),
                "peak_c": peak.get("concurrency", 0),
                "levels": levels,
            }
    return None


def _smoke_section(s, go_data):
    results = [r for r in go_data["results"] if r["ChannelID"] == 30]
    header = ["模型", "模式", "成功率", "E2E p50(ms)", "E2E p95(ms)", "TTFT p95(ms)", "ITL p95(ms)"]
    rows = [header]
    for m in MODELS:
        for streamed in [False, True]:
            for r in results:
                if r["Model"] == m and r["Streamed"] == streamed:
                    e2e, ttft, itl = r["E2E"], r["TTFT"], r["ITL"]
                    rows.append([
                        MODEL_SHORT[m],
                        "流式" if streamed else "非流式",
                        f"{r['SuccessRatePct']:.0f}%",
                        f"{e2e['P50Ms']:.0f}",
                        f"{e2e['P95Ms']:.0f}",
                        f"{ttft['P95Ms']:.0f}" if ttft["Samples"] > 0 else "—",
                        f"{itl['P95Ms']:.1f}" if itl["Samples"] > 0 else "—",
                    ])

    fig, ax = plt.subplots(figsize=(8, 4))
    x = np.arange(len(MODELS))
    stream_p95 = []
    nonstream_p95 = []
    for m in MODELS:
        for r in results:
            if r["Model"] == m and r["Streamed"]:
                stream_p95.append(r["E2E"]["P95Ms"])
            if r["Model"] == m and not r["Streamed"]:
                nonstream_p95.append(r["E2E"]["P95Ms"])
    w = 0.35
    ax.bar(x - w/2, nonstream_p95, w, label="非流式", color=ACCENT, alpha=0.85)
    ax.bar(x + w/2, stream_p95, w, label="流式", color=WARN_COLOR, alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels([MODEL_SHORT[m] for m in MODELS], fontsize=9)
    ax.set_ylabel("E2E p95 (ms)")
    ax.set_title("Smoke 测试 E2E p95 延迟")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    chart = _save_fig(fig, "taiji_smoke")

    return [
        _p("一、连通性 Smoke 测试", "H2", s),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#93C5FD")),
        Spacer(1, 0.2*cm),
        _p("每模型 × 每模式各 3 次请求，并发=2，超时 120s。", "Body", s),
        Spacer(1, 0.3*cm),
        Table(rows, colWidths=[2.5*cm, 2*cm, 2*cm, 2.5*cm, 2.5*cm, 2.5*cm, 2.5*cm], style=_tbl_style()),
        Spacer(1, 0.3*cm),
        Image(str(chart), width=14*cm, height=6.5*cm),
        PageBreak(),
    ]


def _loadtest_section(s, lt_by_model):
    elems = [
        _p("二、并发负载测试", "H2", s),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#93C5FD")),
        Spacer(1, 0.2*cm),
        _p("并发级别 [1, 5, 10, 20, 30, 50, 80, 120, 200, 300, 500, 1000, 2000]，每级 20 请求，超时 300s。", "Body", s),
        Spacer(1, 0.3*cm),
    ]

    for m in MODELS:
        d = lt_by_model.get(m)
        if not d:
            continue
        elems.append(_p(f"模型：{MODEL_SHORT[m]}", "H3", s))
        rows = [["并发", "成功率", "E2E p95(ms)", "TTFT p95(ms)", "吞吐(req/s)"]]
        for lv in d["levels"]:
            sr = lv.get("success_rate_pct", 0)
            e2e_p95 = (lv.get("e2e") or {}).get("p95_ms", 0)
            ttft_p95 = (lv.get("ttft") or {}).get("p95_ms", 0)
            rps = lv.get("throughput_req_per_s", 0)
            sr_str = f"{sr:.0f}%" if sr >= 100 else f"⚠ {sr:.0f}%"
            rows.append([str(lv["concurrency"]), sr_str, f"{e2e_p95:.0f}", f"{ttft_p95:.0f}", f"{rps:.2f}"])
        elems.append(Table(rows, colWidths=[2*cm, 2.5*cm, 3*cm, 3*cm, 3*cm], style=_tbl_style()))
        elems.append(Spacer(1, 0.3*cm))

    summary_rows = [["模型", "总请求", "成功率", "峰值 RPS", "峰值并发"]]
    for m in MODELS:
        d = lt_by_model.get(m)
        if not d:
            continue
        ok, tot = d["total_ok"], d["total_req"]
        rate = 100 * ok / max(tot, 1)
        summary_rows.append([MODEL_SHORT[m], f"{ok}/{tot}", f"{rate:.1f}%",
                             f"{d['peak_rps']:.2f}", str(d['peak_c'])])
    elems.append(_p("汇总", "H3", s))
    elems.append(Table(summary_rows, colWidths=[3*cm, 3*cm, 2.5*cm, 3*cm, 3*cm], style=_tbl_style()))
    elems.append(Spacer(1, 0.4*cm))

    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    for idx, m in enumerate(MODELS):
        ax = axes[idx // 2][idx % 2]
        d = lt_by_model.get(m)
        if d and d.get("levels"):
            cs = [lv["concurrency"] for lv in d["levels"]]
            rps = [lv["throughput_req_per_s"] for lv in d["levels"]]
            ax.plot(cs, rps, marker="o", color=ACCENT, linewidth=2)
        ax.set_title(MODEL_SHORT[m], fontsize=10)
        ax.set_xlabel("并发")
        ax.set_ylabel("RPS")
        ax.grid(alpha=0.3)
    fig.suptitle("吞吐量 vs 并发", fontsize=11)
    fig.tight_layout()
    elems.append(Image(str(_save_fig(fig, "taiji_lt_rps")), width=15*cm, height=11*cm))
    elems.append(PageBreak())
    return elems


_SKIP_PATTERNS = ["no embedding client configured", "no baseline"]


def _is_skipped(p):
    detail = (p.get("detail") or p.get("error") or "")
    return any(pat in detail for pat in _SKIP_PATTERNS)


def _quality_section(s, qa_data):
    per = [p for p in qa_data.get("per_prompt", []) if p["channel"].startswith("1ApiKey")]
    by_model = defaultdict(lambda: {"pass": 0, "total": 0, "skip": 0, "score_sum": 0.0})
    for p in per:
        key = p["model"]
        if _is_skipped(p):
            by_model[key]["skip"] += 1
            continue
        by_model[key]["total"] += 1
        by_model[key]["score_sum"] += p.get("score", 0) or 0
        if p["passed"]:
            by_model[key]["pass"] += 1

    rows = [["模型", "通过/总计", "通过率", "跳过", "平均分"]]
    for m in MODELS:
        d = by_model.get(m)
        if not d or d["total"] == 0:
            rows.append([MODEL_SHORT[m], "—", "—", str(d["skip"] if d else 0), "—"])
            continue
        rate = 100 * d["pass"] / d["total"]
        avg = d["score_sum"] / d["total"]
        rows.append([MODEL_SHORT[m], f"{d['pass']}/{d['total']}",
                     f"{rate:.1f}%", str(d["skip"]), f"{avg:.3f}"])

    fail_rows = [["模型", "类别", "评分器", "详情"]]
    for p in per:
        if p.get("passed") or _is_skipped(p):
            continue
        detail = (p.get("detail") or p.get("error") or "")[:90]
        detail = detail.replace("&", "&amp;").replace("<", "&lt;")
        fail_rows.append([MODEL_SHORT.get(p["model"], p["model"]),
                          p.get("category", ""), p.get("grader", ""),
                          Paragraph(detail, s["Small"])])

    return [
        _p("三、质量评测", "H2", s),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#93C5FD")),
        Spacer(1, 0.2*cm),
        _p("数据集 15 题，双 Judge（sonnet-4-6 + gemini-flash），pass_score=4。"
           "「跳过」= 未配置 embedding 的评分器。", "Body", s),
        Spacer(1, 0.3*cm),
        Table(rows, colWidths=[3.5*cm, 3*cm, 2.5*cm, 2*cm, 2.5*cm], style=_tbl_style()),
        Spacer(1, 0.3*cm),
        _p("失败明细（不含跳过项）", "H3", s),
        Table(fail_rows, colWidths=[2.5*cm, 2.5*cm, 2.5*cm, 10*cm], style=_tbl_style()),
        PageBreak(),
    ]


def _conformance_section(s, cf_by_model):
    rows = [["模型", "通过/总计", "通过率"]]
    for m in MODELS:
        d = cf_by_model.get(m)
        if not d:
            rows.append([MODEL_SHORT[m], "—", "—"])
            continue
        rows.append([MODEL_SHORT[m], f"{d['pass']}/{d['total']}", f"{d['pass_rate']*100:.1f}%"])

    cats = ["param_validation_auto", "param_validation_manual", "messages_structure",
            "auth", "malformed", "openai_features", "tools", "reasoning", "anthropic_messages"]
    cat_rows = [["类别"] + [MODEL_SHORT[m] for m in MODELS]]
    for c in cats:
        row = [c]
        for m in MODELS:
            d = cf_by_model.get(m)
            if not d:
                row.append("—")
            else:
                bc = d.get("by_category", {}).get(c, {})
                p, t = bc.get("pass", 0), bc.get("total", 0)
                row.append(f"{p}/{t}" if t > 0 else "—")
        cat_rows.append(row)

    fig, ax = plt.subplots(figsize=(8, 4))
    x = np.arange(len(MODELS))
    rates = [(cf_by_model.get(m, {"pass_rate": 0})["pass_rate"]) * 100 for m in MODELS]
    ax.bar(x, rates, color=ACCENT, alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels([MODEL_SHORT[m] for m in MODELS], fontsize=9)
    ax.set_ylabel("通过率 (%)")
    ax.set_ylim(85, 100)
    ax.axhline(95, color="gray", linestyle="--", linewidth=0.8)
    ax.set_title("协议合规通过率")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    chart = _save_fig(fig, "taiji_cf")

    return [
        _p("四、协议合规测试", "H2", s),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#93C5FD")),
        Spacer(1, 0.2*cm),
        _p("224 项测试用例覆盖参数校验、消息结构、认证、畸形请求、OpenAI 特性、工具调用、推理、Anthropic Messages 等。", "Body", s),
        Spacer(1, 0.3*cm),
        Table(rows, colWidths=[4*cm, 4*cm, 4*cm], style=_tbl_style()),
        Spacer(1, 0.3*cm),
        Image(str(chart), width=13*cm, height=6*cm),
        Spacer(1, 0.3*cm),
        _p("分类细分", "H3", s),
        Table(cat_rows, colWidths=[4.5*cm, 3*cm, 3*cm, 3*cm, 3*cm], style=_tbl_style()),
        PageBreak(),
    ]


def _conclusions(s, go_data, lt_by_model, cf_by_model):
    elems = [
        _p("五、测试结论与优化建议", "H2", s),
        HRFlowable(width="100%", thickness=2, color=colors.HexColor(WARN_COLOR)),
        Spacer(1, 0.3*cm),
        _p("▶ 总体评价", "H3", s),
        _p("• 可用性优秀：全部 4 模型在 1→2000 并发范围内均保持 100% 成功率，零超时、零错误，稳定性表现突出。", "Bullet", s),
        _p("• 协议合规良好：4 模型平均通过率 92.9%，核心功能（工具调用、推理、OpenAI 特性）全部通过。", "Bullet", s),
        _p("• 质量评测基本达标：排除基础设施限制（embedding 未配置）后，有效通过率约 92%。", "Bullet", s),
        Spacer(1, 0.3*cm),
        _p("▶ 需要优化的问题（按优先级排序）", "H3", s),
        Spacer(1, 0.2*cm),
    ]

    elems += [
        _p("<b>问题 1：吞吐量瓶颈 — 峰值 RPS 偏低（高优先级）</b>", "WarnBullet", s),
        _p("• 现象：4 模型峰值 RPS 均在 5.3~6.6 req/s 之间，即使并发提升到 2000 也无法突破此上限。", "Bullet", s),
        _p("• 影响：对于需要高吞吐的批量调用场景（如批量翻译、数据处理），当前吞吐能力不足。", "Bullet", s),
        _p("• 建议：排查上游是否存在全局 rate limit 或连接池瓶颈；考虑增加并行 key 或多实例分流。", "Bullet", s),
        Spacer(1, 0.2*cm),

        _p("<b>问题 2：Opus 4.7 流式 TTFT 过高且波动大（高优先级）</b>", "WarnBullet", s),
        _p("• 现象：Opus 4.7 流式 TTFT p95 高达 11.0s（Smoke 测试），非流式 E2E p95 达 14.8s；标准差 &gt; 3.6s。", "Bullet", s),
        _p("• 对比：同渠道 Haiku 4.5 TTFT p95 仅 3.3s，Sonnet 4.6 为 7.9s，差距显著。", "Bullet", s),
        _p("• 影响：用户使用 Opus 4.7 时首 token 等待时间过长，体验较差。", "Bullet", s),
        _p("• 建议：确认 Opus 4.7 上游是否存在冷启动/排队问题；考虑预热机制或设置合理的超时预期。", "Bullet", s),
        Spacer(1, 0.2*cm),

        _p("<b>问题 3：高并发下延迟不降反升（中优先级）</b>", "WarnBullet", s),
        _p("• 现象：并发从 50 提升到 2000 时，吞吐量不再增长甚至下降（如 Opus 4.7 从 5.47 降至 3.90 req/s），"
           "E2E 延迟在高并发时出现波动。", "Bullet", s),
        _p("• 影响：说明上游存在排队/限流机制，超过最优并发后请求堆积。", "Bullet", s),
        _p("• 建议：建议客户端并发控制在 50~120 之间为最优区间，超出此范围无收益。", "Bullet", s),
        Spacer(1, 0.2*cm),

        _p("<b>问题 4：param_validation_auto 合规失败 13 项（中优先级）</b>", "WarnBullet", s),
        _p("• 现象：所有 4 模型在 param_validation_auto 类别均有 13/69 项失败（通过率 81.2%）。", "Bullet", s),
        _p("• 影响：越界参数（如 temperature &gt; 2.0、top_p &gt; 1.0）未被拒绝，可能导致不可预期的模型行为。", "Bullet", s),
        _p("• 建议：在网关层增加参数边界校验，对越界值返回 400 错误而非透传给上游。", "Bullet", s),
        Spacer(1, 0.2*cm),

        _p("<b>问题 5：Opus 4.7 指令遵循异常 — 系统提示词干扰（中优先级）</b>", "WarnBullet", s),
        _p("• 现象：Opus 4.7 在 inst-echo-01 测试中拒绝执行简单的回显指令，"
           "返回「I can't discuss that.」而非预期的 'pineapple-42-delta'。", "Bullet", s),
        _p("• 根因：渠道注入的系统提示词触发了模型的安全拒绝机制，导致无害指令被误拦截。", "Bullet", s),
        _p("• 影响：部分依赖精确指令遵循的应用场景（如 agent、自动化工具）可能受影响。", "Bullet", s),
        _p("• 建议：审查并精简注入的系统提示词，避免过度限制模型的正常指令遵循能力。", "Bullet", s),
        Spacer(1, 0.2*cm),

        _p("<b>问题 6：anthropic_messages 类别存在少量失败（低优先级）</b>", "WarnBullet", s),
        _p("• 现象：4 模型在 anthropic_messages 类别各有 3 项失败（83/86 通过，通过率 96.5%）。", "Bullet", s),
        _p("• 影响：对使用 Anthropic 原生 Messages API 格式的客户端可能存在兼容性问题。", "Bullet", s),
        _p("• 建议：排查失败用例的具体场景，确认是否为已知限制或可修复的协议转换问题。", "Bullet", s),
        Spacer(1, 0.2*cm),

        _p("<b>问题 7：Prompt Token 膨胀 — 计费成本偏高（低优先级）</b>", "WarnBullet", s),
        _p("• 现象：相同的测试 prompt，实际消耗的 prompt_tokens 显著高于 prompt 本身长度"
           "（如简单数学题消耗 600+ tokens），说明渠道注入了较长的系统提示词。", "Bullet", s),
        _p("• 影响：用户实际计费成本高于预期，尤其对短 prompt 高频调用场景影响明显。", "Bullet", s),
        _p("• 建议：评估系统提示词的必要性，尽量精简；或在计费时扣除系统提示词部分的 token 消耗。", "Bullet", s),
        Spacer(1, 0.4*cm),
    ]

    elems += [
        _p("▶ 优势总结", "H3", s),
        _p("• 稳定性极佳：2000 并发零失败，适合对可用性要求高的生产环境。", "Bullet", s),
        _p("• 模型覆盖完整：4 个 Claude 模型全部可用且功能正常。", "Bullet", s),
        _p("• 核心功能合规：工具调用、推理、OpenAI 特性、认证等关键类别 100% 通过。", "Bullet", s),
        _p("• 安全性表现好：safety 类评测中模型拒绝行为符合预期。", "Bullet", s),
    ]

    return elems


def main():
    go_data = _load(GO_JSON)

    lt_by_model = {}
    for m, p in LT_FILES.items():
        if not p.exists():
            print(f"WARN: missing {p}")
            continue
        lt_by_model[m] = _parse_lt_ch30(_load(p))

    qa_data = _load(QA_JSON)

    cf_by_model = {}
    for m, p in CF_FILES.items():
        if p.exists():
            cf_by_model[m] = _load(p)

    s = _styles()
    story = []
    story += _cover(s)
    story += _conclusions(s, go_data, lt_by_model, cf_by_model)
    story += _smoke_section(s, go_data)
    story += _loadtest_section(s, lt_by_model)
    story += _quality_section(s, qa_data)
    story += _conformance_section(s, cf_by_model)

    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT_PDF), pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )
    doc.build(story)
    print(f"wrote {OUT_PDF}")


if __name__ == "__main__":
    main()
