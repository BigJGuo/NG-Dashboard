"""Build a multi-page PDF snapshot of the dashboard charts using kaleido + reportlab."""
from __future__ import annotations

import datetime as dt
import io

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak

from tabs import storage as tab_storage
from tabs import weather as tab_weather
from tabs import news as tab_news
from tabs import positioning as tab_positioning
from tabs import curve as tab_curve


def _fig_to_png_bytes(fig, width=1400, height=600):
    try:
        return fig.to_image(format="png", width=width, height=height, engine="kaleido")
    except Exception:
        return None


def build_pdf_snapshot(stores: dict) -> bytes:
    """Return a PDF byte string built from current dashboard state."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter),
                            leftMargin=0.4 * inch, rightMargin=0.4 * inch,
                            topMargin=0.4 * inch, bottomMargin=0.4 * inch)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], textColor=HexColor("#00ff88"),
                        fontSize=18, leading=22)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], textColor=HexColor("#ffffff"),
                        fontSize=12, leading=16)
    meta = ParagraphStyle("Meta", parent=styles["Normal"], textColor=HexColor("#888888"),
                          fontSize=9, leading=12)

    elements = []
    elements.append(Paragraph("NG Trading Intelligence — Snapshot", h1))
    elements.append(Paragraph(f"Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", meta))
    elements.append(Spacer(1, 0.2 * inch))

    tab_modules = [
        ("Storage",     tab_storage),
        ("Weather",     tab_weather),
        ("News",        tab_news),
        ("Positioning", tab_positioning),
        ("Curve",       tab_curve),
    ]

    for tab_label, module in tab_modules:
        figs = []
        try:
            figs = module.figures_for_snapshot(stores)
        except Exception:
            figs = []
        elements.append(PageBreak())
        elements.append(Paragraph(f"Tab — {tab_label}", h1))
        elements.append(Spacer(1, 0.1 * inch))
        if not figs:
            elements.append(Paragraph("(no data captured for this tab)", meta))
            continue
        for title, fig in figs:
            png = _fig_to_png_bytes(fig)
            elements.append(Paragraph(title, h2))
            if png:
                elements.append(Image(io.BytesIO(png), width=9.5 * inch, height=4.2 * inch))
            else:
                elements.append(Paragraph("(chart render failed — kaleido unavailable)", meta))
            elements.append(Spacer(1, 0.15 * inch))

    doc.build(elements)
    return buf.getvalue()
