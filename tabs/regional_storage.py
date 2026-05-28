"""Regional Storage Breakdown — choropleth map + per-region metric tiles.

Phase 1 of the regional storage feature. Sits below the national storage charts
inside the Storage tab. Renders:
  - A US choropleth colored by each EIA region's deviation from its 5y average
    (toggleable between % vs 5y avg, Bcf vs 5y avg, YoY Bcf, WoW Bcf).
  - A row of 5 metric cards (East, Midwest, Mountain, Pacific, South Central),
    each with current Bcf, deviation, and an 8-week sparkline. The South
    Central card splits Salt vs Non-Salt into two sub-rows.

Refresh cadence is dynamic: 60s during the EIA release window (Thursdays
10–12 ET), 10m otherwise.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import pytz
from dash import dcc, html, Input, Output, State, no_update
import dash_bootstrap_components as dbc

import config
from data import eia
from utils.theme import COLORS, plotly_layout

NY = pytz.timezone("America/New_York")

_REGIONS = ["East", "Midwest", "Mountain", "Pacific", "South Central"]

_MAP_MODES = [
    {"label": "% vs 5yr Avg",     "value": "dev_pct"},
    {"label": "Bcf vs 5yr Avg",   "value": "dev_bcf"},
    {"label": "YoY Δ (Bcf)",      "value": "yoy_bcf"},
    {"label": "WoW Δ (Bcf)",      "value": "wow_bcf"},
]
_MODE_COLORBAR = {
    "dev_pct": "% vs 5yr Avg",
    "dev_bcf": "Bcf vs 5yr Avg",
    "yoy_bcf": "YoY Δ (Bcf)",
    "wow_bcf": "WoW Δ (Bcf)",
}
# High-contrast diverging scale for the choropleth. Endpoints are deep
# saturated reds / forest greens so extreme regions visually punch out; mid
# stops keep the gradient readable for states in the ±5–15% range.
_DIVERGING_COLORSCALE = [
    [0.0,  "#660000"],   # max deficit — very deep crimson
    [0.25, "#cc0000"],   # mid deficit — saturated red
    [0.5,  "#ffffff"],   # neutral
    [0.75, "#008833"],   # mid surplus — saturated forest green
    [1.0,  "#003311"],   # max surplus — very deep forest
]


# ── Layout ────────────────────────────────────────────────────────────────────

def layout():
    return html.Div([
        dcc.Interval(id="regional-storage-interval",
                     interval=config.REGIONAL_STORAGE_SLOW_MS),
        html.H4("Regional Storage Breakdown",
                style={"color": COLORS["TEXT"], "marginTop": "32px",
                       "marginBottom": "4px"}),
        html.Div(id="regional-storage-last-updated",
                 style={"fontSize": "12px", "color": COLORS["MUTED"],
                        "marginBottom": "12px"}),
        dbc.RadioItems(
            id="regional-map-mode",
            options=_MAP_MODES,
            value="dev_pct",
            inline=True,
            inputClassName="me-1",
            labelClassName="me-3",
            style={"marginBottom": "8px"},
        ),
        dcc.Graph(id="regional-choropleth-map", style={"height": "500px"}),
        dbc.Row(id="regional-metric-cards", style={"marginTop": "16px"}),
    ])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_bcf(v):
    return "—" if v is None else f"{v:+,.0f}"


def _fmt_pct(v):
    return "—" if v is None else f"{v:+.1f}%"


def _color_for(v):
    if v is None:
        return COLORS["MUTED"]
    return COLORS["BULL"] if v > 0 else (COLORS["BEAR"] if v < 0 else COLORS["TEXT"])


def _build_sparkline_fig(values: list, dev_bcf) -> go.Figure:
    fig = go.Figure()
    if not values:
        fig.update_layout(
            paper_bgcolor=COLORS["PANEL"], plot_bgcolor=COLORS["PANEL"],
            margin={"l": 0, "r": 0, "t": 0, "b": 0}, height=60, showlegend=False,
        )
        return fig
    line_color = (COLORS["BULL"] if (dev_bcf is not None and dev_bcf >= 0)
                  else COLORS["BEAR"])
    xs = list(range(len(values)))
    fig.add_trace(go.Scatter(
        x=xs, y=values, mode="lines",
        line=dict(color=line_color, width=2),
        hoverinfo="skip",
    ))
    fig.update_layout(
        paper_bgcolor=COLORS["PANEL"], plot_bgcolor=COLORS["PANEL"],
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        height=60, showlegend=False,
        xaxis=dict(visible=False, fixedrange=True),
        yaxis=dict(visible=False, fixedrange=True),
    )
    return fig


def _region_subline(region_name: str, stats: dict):
    """Render the small numeric line under a region card header.

    `stats` is a single region's stats dict; may be None if no data yet.
    """
    if not stats:
        return html.Div("Awaiting data", style={"color": COLORS["MUTED"], "fontSize": "12px"})
    current = stats.get("current_bcf")
    dev_bcf = stats.get("dev_bcf")
    dev_pct = stats.get("dev_pct")
    color = _color_for(dev_bcf)
    return html.Div([
        html.Div(f"{current:,.0f} Bcf" if current is not None else "—",
                 style={"fontSize": "20px", "fontWeight": "700",
                        "color": COLORS["TEXT"]}),
        html.Div(f"{_fmt_bcf(dev_bcf)} Bcf  ({_fmt_pct(dev_pct)})",
                 style={"fontSize": "12px", "color": color, "fontWeight": "600"}),
    ])


def _metric_card_region(region_name: str, region_stats: dict | None,
                        salt_stats: dict | None = None,
                        nonsalt_stats: dict | None = None) -> Any:
    """Build one region's card. For South Central, pass salt_stats + nonsalt_stats
    to render the split sub-layout."""
    header = html.Div(region_name,
                      style={"fontSize": "13px", "fontWeight": "700",
                             "color": COLORS["MUTED"], "letterSpacing": "0.5px",
                             "marginBottom": "6px"})

    if region_name == "South Central" and (salt_stats or nonsalt_stats):
        body = html.Div([
            _region_subline("South Central", region_stats),
            dcc.Graph(
                figure=_build_sparkline_fig(
                    (region_stats or {}).get("sparkline_8w") or [],
                    (region_stats or {}).get("dev_bcf"),
                ),
                config={"displayModeBar": False},
                style={"height": "60px", "marginTop": "4px"},
            ),
            html.Hr(style={"borderColor": COLORS["GRID"], "margin": "8px 0"}),
            html.Div("Salt",
                     style={"fontSize": "11px", "fontWeight": "700",
                            "color": COLORS["MUTED"], "letterSpacing": "0.5px"}),
            _region_subline("Salt", salt_stats),
            html.Div("Non-Salt",
                     style={"fontSize": "11px", "fontWeight": "700",
                            "color": COLORS["MUTED"], "letterSpacing": "0.5px",
                            "marginTop": "8px"}),
            _region_subline("Non-Salt", nonsalt_stats),
        ])
    else:
        body = html.Div([
            _region_subline(region_name, region_stats),
            dcc.Graph(
                figure=_build_sparkline_fig(
                    (region_stats or {}).get("sparkline_8w") or [],
                    (region_stats or {}).get("dev_bcf"),
                ),
                config={"displayModeBar": False},
                style={"height": "60px", "marginTop": "4px"},
            ),
        ])

    card = dbc.Card(dbc.CardBody([header, body]),
                    className="metric-card",
                    style={"backgroundColor": COLORS["PANEL"],
                           "border": f"1px solid {COLORS['GRID']}",
                           "height": "100%"})
    # South Central is the wider card because it carries the split sub-rows.
    width = {"width": 3} if region_name == "South Central" else {"width": True}
    return dbc.Col(card, **width)


def _build_choropleth_fig(payload: dict | None, mode: str) -> go.Figure:
    fig = go.Figure()
    layout_kwargs = plotly_layout(showlegend=False)
    # Geo charts ignore xaxis/yaxis grid settings — strip them.
    layout_kwargs.pop("xaxis", None)
    layout_kwargs.pop("yaxis", None)
    layout_kwargs["geo"] = dict(
        scope="usa",
        bgcolor=COLORS["PANEL"],
        lakecolor=COLORS["BG"],
        landcolor=COLORS["BG"],
        showlakes=True,
        showcoastlines=False,
        showframe=False,
    )
    layout_kwargs["margin"] = {"l": 0, "r": 0, "t": 10, "b": 0}
    fig.update_layout(**layout_kwargs)

    if not payload:
        fig.add_annotation(text="Awaiting regional storage data…",
                           showarrow=False,
                           font=dict(color=COLORS["MUTED"], size=14))
        return fig

    # Collect per-region values for the chosen mode, then expand to per-state.
    region_value: dict[str, float] = {}
    for region in _REGIONS:
        rec = payload.get(region) or {}
        stats = rec.get("stats") or {}
        v = stats.get(mode)
        region_value[region] = float(v) if v is not None else None  # type: ignore

    # zmin/zmax: ±30 fixed for dev_pct, symmetric data-driven otherwise.
    if mode == "dev_pct":
        zmin, zmax = -30.0, 30.0
    else:
        vals = [abs(v) for v in region_value.values() if v is not None]
        max_abs = max(vals) if vals else 1.0
        max_abs = max(max_abs, 1.0)
        zmin, zmax = -max_abs, max_abs

    locations: list[str] = []
    z: list[float | None] = []
    customdata: list[list] = []
    for state, region in config.STATE_TO_STORAGE_REGION.items():
        rec = payload.get(region) or {}
        stats = rec.get("stats") or {}
        if not stats:
            continue
        raw = region_value.get(region)
        z_capped = None
        if raw is not None:
            z_capped = max(zmin, min(zmax, raw))
        def _f0(v): return "—" if v is None else f"{v:+,.0f}"
        def _u0(v): return "—" if v is None else f"{v:,.0f}"
        def _f1(v): return "—" if v is None else f"{v:+.1f}"
        locations.append(state)
        z.append(z_capped)
        customdata.append([
            region,
            _u0(stats.get("current_bcf")),
            _u0(stats.get("avg_5y_bcf")),
            _f0(stats.get("dev_bcf")),
            _f1(stats.get("dev_pct")),
            _f0(stats.get("yoy_bcf")),
            _f0(stats.get("wow_bcf")),
        ])

    hovertemplate = (
        "<b>%{location}</b><br>"
        "Region: %{customdata[0]}<br>"
        "Current: %{customdata[1]} Bcf<br>"
        "5yr Avg: %{customdata[2]} Bcf<br>"
        "Δ vs 5yr: %{customdata[3]} Bcf (%{customdata[4]}%)<br>"
        "YoY: %{customdata[5]} Bcf<br>"
        "WoW: %{customdata[6]} Bcf"
        "<extra></extra>"
    )

    fig.add_trace(go.Choropleth(
        locations=locations,
        locationmode="USA-states",
        z=z,
        customdata=customdata,
        colorscale=_DIVERGING_COLORSCALE,
        zmin=zmin, zmax=zmax,
        marker_line_color=COLORS["BG"],
        marker_line_width=0.5,
        colorbar=dict(
            title=dict(text=_MODE_COLORBAR.get(mode, ""),
                       font=dict(color=COLORS["TEXT"], size=12)),
            tickfont=dict(color=COLORS["TEXT"]),
            tickformat=".0f",
            outlinecolor=COLORS["GRID"],
            bgcolor=COLORS["PANEL"],
            thickness=12, len=0.85,
        ),
        hovertemplate=hovertemplate,
    ))
    return fig


# ── Callbacks ─────────────────────────────────────────────────────────────────

def register_callbacks(app):

    @app.callback(
        Output("regional-storage-interval", "interval"),
        Input("clock-interval", "n_intervals"),
        State("regional-storage-interval", "interval"),
    )
    def update_regional_interval(_n, current):
        now = dt.datetime.now(NY)
        if now.weekday() == 3 and now.hour in (10, 11):
            target = config.REGIONAL_STORAGE_FAST_MS
        else:
            target = config.REGIONAL_STORAGE_SLOW_MS
        return target if current != target else no_update

    @app.callback(
        Output("regional-storage-data", "data"),
        Input("regional-storage-interval", "n_intervals"),
        State("settings-store", "data"),
    )
    def fetch_regional_storage_data(_n, settings):
        api_key = (settings or {}).get("eia_key") or config.EIA_API_KEY_FALLBACK
        region_frames = eia.fetch_regional_storage(api_key)
        if not region_frames:
            return no_update
        out: dict = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        for region_name, df in region_frames.items():
            stats = eia.calculate_regional_stats(df)
            # Persist a compact form of the series — only need to keep enough
            # for Phase 2 (small multiples) and the card sparklines.
            series = df.tail(260).reset_index().to_dict("records")
            for r in series:
                r["period"] = (r["period"].isoformat()
                               if hasattr(r["period"], "isoformat")
                               else str(r["period"]))
            out[region_name] = {"series": series, "stats": stats}
        return out

    @app.callback(
        Output("regional-choropleth-map", "figure"),
        Input("regional-storage-data", "data"),
        Input("regional-map-mode", "value"),
    )
    def update_choropleth_map(data, mode):
        return _build_choropleth_fig(data, mode or "dev_pct")

    @app.callback(
        Output("regional-metric-cards", "children"),
        Output("regional-storage-last-updated", "children"),
        Input("regional-storage-data", "data"),
    )
    def update_regional_metric_cards(data):
        if not data:
            return [], "Awaiting first regional fetch…"
        salt = (data.get("South Central Salt") or {}).get("stats")
        nonsalt = (data.get("South Central Non-Salt") or {}).get("stats")
        cards = []
        for region in _REGIONS:
            rec = data.get(region) or {}
            stats = rec.get("stats")
            if region == "South Central":
                cards.append(_metric_card_region(
                    region, stats,
                    salt_stats=salt, nonsalt_stats=nonsalt,
                ))
            else:
                cards.append(_metric_card_region(region, stats))
        fetched = data.get("fetched_at")
        stamp = "Awaiting first regional fetch…"
        if fetched:
            try:
                ts = pd.to_datetime(fetched).tz_convert(NY).strftime("%Y-%m-%d %H:%M:%S %Z")
            except Exception:
                ts = str(fetched)
            stamp = f"Last regional fetch: {ts}"
        return cards, stamp
