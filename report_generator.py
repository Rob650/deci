"""
report_generator.py — PDF report generation for Deci using ReportLab.
"""
import html as html_module
import datetime
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

# ── Brand colors ──────────────────────────────────────────────────────────────
DECI_BLUE = colors.HexColor("#0891b2")
DECI_DARK = colors.HexColor("#0f172a")
ACCENT_BG = colors.HexColor("#f0f9ff")
MUTED = colors.HexColor("#64748b")
BORDER = colors.HexColor("#cbd5e1")
GREEN = colors.HexColor("#16a34a")
AMBER = colors.HexColor("#d97706")
ORANGE = colors.HexColor("#ea580c")
RED = colors.HexColor("#dc2626")


def _score_color(score: float) -> colors.Color:
    s = float(score)
    if s >= 8:
        return GREEN
    if s >= 6:
        return AMBER
    if s >= 4:
        return ORANGE
    return RED


def _safe(text: str) -> str:
    """Escape text for ReportLab Paragraph (converts newlines to <br/>)."""
    text = text.replace("•", "-").replace("\u2022", "-")
    text = html_module.escape(text, quote=False)
    text = text.replace("\n", "<br/>")
    return text


def generate_pdf_report(
    url: str,
    report: dict,
    sources: dict,
    pages_scraped: int,
    created_at: str | None = None,
) -> bytes:
    buf = BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2.0 * cm,
        rightMargin=2.0 * cm,
        topMargin=2.0 * cm,
        bottomMargin=2.2 * cm,
        title="Deci Intelligence Report",
        author="Deci",
    )

    # ── Styles ────────────────────────────────────────────────────────────────
    def style(name, **kwargs):
        return ParagraphStyle(name, **kwargs)

    title_s = style("DeciTitle", fontName="Helvetica-Bold", fontSize=24,
                    textColor=DECI_BLUE, leading=28, spaceAfter=2)
    sub_s = style("DeciSub", fontName="Helvetica", fontSize=9,
                  textColor=MUTED, spaceAfter=4)
    proj_s = style("ProjName", fontName="Helvetica-Bold", fontSize=14,
                   textColor=DECI_DARK, spaceAfter=2)
    meta_r = style("MetaRight", fontName="Helvetica", fontSize=7.5,
                   textColor=MUTED, alignment=TA_RIGHT)
    sec_s = style("SecHeader", fontName="Helvetica-Bold", fontSize=10,
                  textColor=DECI_BLUE, spaceBefore=14, spaceAfter=6,
                  borderPad=0)
    body_s = style("Body", fontName="Helvetica", fontSize=9,
                   textColor=DECI_DARK, leading=14, spaceAfter=6)
    exec_s = style("Exec", fontName="Helvetica-BoldOblique", fontSize=9.5,
                   textColor=DECI_DARK, leading=15, backColor=ACCENT_BG,
                   leftIndent=8, rightIndent=8, spaceBefore=4, spaceAfter=10,
                   borderPad=6)
    src_s = style("Src", fontName="Helvetica", fontSize=7.5, textColor=MUTED,
                  spaceAfter=12)
    foot_s = style("Foot", fontName="Helvetica-Oblique", fontSize=7,
                   textColor=MUTED, alignment=TA_CENTER, spaceBefore=8)
    score_h = style("ScoreH", fontName="Helvetica-Bold", fontSize=8,
                    textColor=MUTED, alignment=TA_CENTER)

    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(Paragraph("DECI", title_s))
    story.append(Paragraph("Project Intelligence Report", sub_s))
    story.append(HRFlowable(width="100%", thickness=2, color=DECI_BLUE,
                            spaceAfter=10, spaceBefore=4))

    # Project name + meta
    project_name = (sources or {}).get("_meta", {}).get("project_name", "Unknown Project")
    date_str = (created_at or "")[:10] or datetime.date.today().isoformat()

    meta_table = Table(
        [[
            Paragraph(project_name, proj_s),
            Paragraph(
                f"Date: {date_str}<br/>Pages scraped: {pages_scraped}<br/>{url}",
                meta_r,
            ),
        ]],
        colWidths=["55%", "45%"],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]),
    )
    story.append(meta_table)
    story.append(Spacer(1, 10))

    # ── Score table ───────────────────────────────────────────────────────────
    scores = report.get("scores", {})
    score_defs = [
        ("Team", "team"),
        ("Technology", "technology"),
        ("Tokenomics", "tokenomics"),
        ("Community", "community"),
        ("Execution", "execution"),
        ("OVERALL", "overall"),
    ]

    hdr_row = [Paragraph(label, score_h) for label, _ in score_defs]
    val_row = []
    for label, key in score_defs:
        val = scores.get(key, 5)
        c = _score_color(float(val))
        val_row.append(
            Paragraph(
                f"<font color='#{c.hexval()[2:]}'><b>{val}/10</b></font>",
                style(f"SV_{key}", fontName="Helvetica-Bold", fontSize=11,
                      alignment=TA_CENTER),
            )
        )

    score_table = Table(
        [hdr_row, val_row],
        colWidths=["16.66%"] * 6,
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("BACKGROUND", (5, 0), (5, 1), ACCENT_BG),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]),
    )
    story.append(score_table)
    story.append(Spacer(1, 12))

    # ── Executive summary ─────────────────────────────────────────────────────
    exec_text = (report.get("executive_summary") or "").strip()
    if exec_text:
        story.append(Paragraph("EXECUTIVE SUMMARY", sec_s))
        story.append(Paragraph(_safe(exec_text), exec_s))

    # ── Data source badges ────────────────────────────────────────────────────
    src_map = {
        "Website": True,
        "GitHub": (sources or {}).get("github", {}).get("available", False),
        "CoinGecko": (sources or {}).get("coingecko", {}).get("available", False),
        "DeFiLlama": (sources or {}).get("defillama", {}).get("available", False),
        "News Search": (sources or {}).get("news", {}).get("available", False),
        "Twitter/X": (sources or {}).get("twitter", {}).get("available", False),
    }
    src_parts = []
    for label, avail in src_map.items():
        mark = "[OK]" if avail else "[-]"
        src_parts.append(f"{mark} {label}")

    story.append(Paragraph("DATA SOURCES: " + "  |  ".join(src_parts), src_s))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=8))

    # ── Content sections ──────────────────────────────────────────────────────
    sections = [
        ("RECENT EVENTS & NEWS", "recent_events"),
        ("PROJECT FOCUS & TECHNOLOGY", "project_focus"),
        ("TEAM & BACKERS", "team"),
        ("TOKENOMICS", "tokenomics"),
        ("ROADMAP & MILESTONES", "roadmap"),
        ("UNIQUE VALUE PROPOSITION", "unique_value_proposition"),
        ("COMPETITIVE LANDSCAPE", "competitive_landscape"),
        ("COMMUNITY & SOCIAL", "community_social"),
        ("ON-CHAIN METRICS", "on_chain_metrics"),
        ("RISK ASSESSMENT", "risk_assessment"),
    ]

    for title, key in sections:
        text = (report.get(key) or "Not available.").strip()
        if not text or text in ("N/A", "Not available."):
            continue
        story.append(Paragraph(title, sec_s))
        story.append(Paragraph(_safe(text), body_s))

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
    story.append(Paragraph(
        "Generated by DECI — For research purposes only. Not financial advice. "
        "Data sourced from public APIs and web search.",
        foot_s,
    ))

    doc.build(story)
    return buf.getvalue()
