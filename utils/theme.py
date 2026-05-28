"""Shared visual theme for the dashboard.

Every Plotly figure should apply ``plotly_layout()`` so all charts share the
same dark background, grid color, and font. Colors are referenced by name from
``COLORS`` rather than hard-coded in tab modules.
"""

COLORS = {
    "BG":      "#0d0d0d",
    "PANEL":   "#1a1a1a",
    "GRID":    "#2a2a2a",
    "TEXT":    "#ffffff",
    "MUTED":   "#888888",
    "BULL":    "#00ff88",
    "BEAR":    "#ff3333",
    "WARN":    "#ffcc00",
    "ORANGE":  "#ff9933",
    "BLUE":    "#3399ff",
}


def plotly_layout(title=None, height=None, showlegend=True):
    layout = {
        "paper_bgcolor": COLORS["PANEL"],
        "plot_bgcolor":  COLORS["PANEL"],
        "font": {"color": COLORS["TEXT"], "family": "Segoe UI, Arial, sans-serif", "size": 12},
        "xaxis": {"gridcolor": COLORS["GRID"], "zerolinecolor": COLORS["GRID"], "color": COLORS["TEXT"]},
        "yaxis": {"gridcolor": COLORS["GRID"], "zerolinecolor": COLORS["GRID"], "color": COLORS["TEXT"]},
        "margin": {"l": 50, "r": 30, "t": 50 if title else 20, "b": 40},
        "showlegend": showlegend,
        "legend": {"bgcolor": "rgba(0,0,0,0)", "font": {"color": COLORS["TEXT"]}},
    }
    if title:
        layout["title"] = {"text": title, "font": {"color": COLORS["TEXT"], "size": 16}}
    if height:
        layout["height"] = height
    return layout


def bull_or_bear(value, neutral_threshold=0.0):
    if value is None:
        return COLORS["MUTED"]
    if value > neutral_threshold:
        return COLORS["BULL"]
    if value < -neutral_threshold:
        return COLORS["BEAR"]
    return COLORS["MUTED"]


def colored_change(value, pct=None, prefix=""):
    """Return an HTML-safe string and color for a change indicator."""
    color = bull_or_bear(value)
    arrow = "▲" if (value or 0) > 0 else ("▼" if (value or 0) < 0 else "■")
    text = f"{prefix}{arrow} {value:+.2f}"
    if pct is not None:
        text += f" ({pct:+.2f}%)"
    return text, color
