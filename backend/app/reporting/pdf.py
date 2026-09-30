"""PDF building blocks (reportlab platypus + matplotlib charts rendered to PNG)."""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

INK = colors.HexColor("#1b2230")
MUTED = colors.HexColor("#5b6475")
RULE = colors.HexColor("#d5d9e0")
ACCENT = "#2563eb"
BENCH = "#8a93a3"
NEG = "#c2410c"

_styles = getSampleStyleSheet()
STYLES = {
    "title": ParagraphStyle(
        "t",
        parent=_styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=INK,
        alignment=TA_LEFT,
        spaceAfter=4,
    ),
    "subtitle": ParagraphStyle("st", parent=_styles["Normal"], fontSize=10, textColor=MUTED, spaceAfter=10),
    "h1": ParagraphStyle(
        "h1", parent=_styles["Heading1"], fontName="Helvetica-Bold", fontSize=14, textColor=INK, spaceBefore=10, spaceAfter=6
    ),
    "h2": ParagraphStyle(
        "h2", parent=_styles["Heading2"], fontName="Helvetica-Bold", fontSize=11, textColor=INK, spaceBefore=8, spaceAfter=4
    ),
    "body": ParagraphStyle("b", parent=_styles["Normal"], fontSize=9, leading=12.5, textColor=INK),
    "small": ParagraphStyle("s", parent=_styles["Normal"], fontSize=7.5, leading=10, textColor=MUTED),
    "banner": ParagraphStyle(
        "banner",
        parent=_styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=colors.HexColor("#92400e"),
        backColor=colors.HexColor("#fef3c7"),
        borderPadding=5,
        spaceBefore=4,
        spaceAfter=10,
    ),
}


def pct(v: Any, digits: int = 2, signed: bool = True) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.{digits}%}" if signed else f"{v:.{digits}%}"


def num(v: Any, digits: int = 3) -> str:
    return "n/a" if v is None else f"{v:,.{digits}f}"


def money(v: Any) -> str:
    return "n/a" if v is None else f"{v:,.0f}"


def para(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, STYLES[style])


def table(
    rows: Sequence[Sequence[Any]], col_widths: Sequence[float] | None = None, header: bool = True, align_right_from: int = 1
) -> Table:
    data = [
        [Paragraph(str(c), STYLES["small"] if i else STYLES["body"]) if isinstance(c, str) and len(c) > 40 else c for c in row]
        for i, row in enumerate(rows)
    ]
    t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, RULE),
        ("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]
    if header:
        style.append(("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8))
    t.setStyle(TableStyle(style))
    return t


def _fig_to_image(fig: Any, width_mm: float = 170) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    img = Image(buf)
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width_mm * mm
    img.drawHeight = width_mm * mm * ratio
    return img


def _base_ax(title: str, figsize: tuple[float, float] = (8, 2.8)) -> tuple[Any, Any]:
    fig, ax = plt.subplots(figsize=figsize)
    ax.set_title(title, fontsize=9, loc="left", color="#1b2230")
    ax.tick_params(labelsize=7, colors="#5b6475")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c5cad3")
    ax.grid(True, color="#eceef2", linewidth=0.6)
    return fig, ax


def _compact(v: float, _pos: int | None = None) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.1f}B"
    if a >= 1e6:
        return f"{v / 1e6:.2f}M"
    if a >= 1e4:
        return f"{v / 1e3:.0f}k"
    return f"{v:,.2f}"


def line_chart(
    dates: list[str],
    series: dict[str, list[float | None]],
    title: str,
    percent: bool = False,
    fill_negative: bool = False,
    date_fmt: str | None = None,
) -> Image:
    fig, ax = _base_ax(title)
    x = pd.to_datetime(dates)
    palette = [ACCENT, BENCH, "#0f766e", "#b45309", "#7c3aed", "#be123c", "#0891b2", "#4d7c0f"]
    for i, (name, ys) in enumerate(series.items()):
        y = pd.Series(ys, dtype=float)
        ax.plot(x, y, lw=1.1, color=palette[i % len(palette)], label=name)
        if fill_negative:
            ax.fill_between(x, y, 0, color=NEG, alpha=0.15)
    if percent:
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    else:
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(_compact))
    if date_fmt is None and len(x) > 1:
        span = (x.max() - x.min()).days
        date_fmt = "%d %b %H:%M" if span <= 7 else "%d %b" if span <= 120 else "%b %Y" if span <= 800 else "%Y"
    ax.xaxis.set_major_formatter(mdates.DateFormatter(date_fmt or "%Y"))
    fig.autofmt_xdate(rotation=0, ha="center")
    if len(series) > 1:
        ax.legend(fontsize=7, frameon=False, loc="upper left")
    return _fig_to_image(fig)


def bar_chart(labels: list[str], values: list[float], title: str, percent: bool = False, horizontal: bool = True) -> Image:
    fig, ax = _base_ax(title, (8, max(2.0, 0.22 * len(labels) + 0.8)))
    cols = [NEG if v < 0 else ACCENT for v in values]
    if horizontal:
        ax.barh(labels, values, color=cols)
        ax.invert_yaxis()
        if percent:
            ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=1))
    else:
        ax.bar(labels, values, color=cols)
        if percent:
            ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=1))
    return _fig_to_image(fig)


def monthly_heatmap(rows: list[dict[str, Any]], title: str) -> Image | None:
    if not rows:
        return None
    df = pd.DataFrame(rows).pivot(index="year", columns="month", values="return").reindex(columns=range(1, 13))
    fig, ax = plt.subplots(figsize=(8, 0.35 * len(df) + 0.9))
    lim = max(0.05, float(df.abs().max().max()))
    im = ax.imshow(df.to_numpy(dtype=float), cmap="RdBu", vmin=-lim, vmax=lim, aspect="auto")
    ax.set_xticks(range(12), ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"], fontsize=7)
    ax.set_yticks(range(len(df)), [str(y) for y in df.index], fontsize=7)
    for i in range(len(df)):
        for j in range(12):
            v = df.iat[i, j]
            if pd.notna(v):
                ax.text(j, i, f"{v * 100:.1f}", ha="center", va="center", fontsize=5.5, color="#1b2230")
    ax.set_title(title, fontsize=9, loc="left")
    fig.colorbar(im, ax=ax, fraction=0.02, format=matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    return _fig_to_image(fig)


def build_pdf(path: str, story: list[Any], title: str, footer_note: str) -> None:
    def on_page(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, f"Nexis Finance · {title}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.drawString(18 * mm, 6.5 * mm, footer_note[:150])
        canvas.restoreState()

    doc = SimpleDocTemplate(
        path,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="Nexis Finance",
    )
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


__all__ = [
    "KeepTogether",
    "PageBreak",
    "Spacer",
    "bar_chart",
    "build_pdf",
    "line_chart",
    "mm",
    "money",
    "monthly_heatmap",
    "num",
    "para",
    "pct",
    "table",
]
