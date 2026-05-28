"""Tab 2: 30-city weather map, regional HDD, revision leaderboard, demand."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html, Input, Output, State, dash_table, no_update
import dash_bootstrap_components as dbc

import config
from data import weather as wdata
from utils.theme import COLORS, plotly_layout


MAP_MODES = [
    {"label": "Temperature",     "value": "temp"},
    {"label": "HDD",             "value": "hdd"},
    {"label": "Revision 24h",    "value": "rev24"},
    {"label": "Dev from Normal", "value": "devn"},
]


def layout():
    return html.Div([
        dcc.Interval(id="weather-interval", interval=config.DEFAULT_INTERVALS["weather_ms"]),
        dbc.Row([
            dbc.Col(dbc.RadioItems(id="weather-mode", options=MAP_MODES, value="temp",
                                   inline=True, inputClassName="me-1", labelClassName="me-3"),
                    width=8),
            dbc.Col(html.Div(id="weather-last-update",
                             style={"color": COLORS["MUTED"], "fontSize": "12px",
                                    "textAlign": "right", "paddingTop": "4px"}),
                    width=4),
        ], style={"marginBottom": "8px"}),
        dbc.Row([
            dbc.Col(dcc.Graph(id="weather-map"), width=8),
            dbc.Col([
                html.H6("Forecast Revision Leaderboard",
                        style={"color": COLORS["TEXT"]}),
                dash_table.DataTable(
                    id="weather-revision-table",
                    columns=[
                        {"name": "City",      "id": "name"},
                        {"name": "Δ vs 24h", "id": "rev24"},
                        {"name": "Δ vs 72h", "id": "rev72"},
                    ],
                    style_table={"height": "480px", "overflowY": "auto"},
                    style_cell={"backgroundColor": COLORS["PANEL"], "color": COLORS["TEXT"],
                                "border": f"1px solid {COLORS['GRID']}", "fontSize": "12px",
                                "padding": "4px 8px"},
                    style_header={"backgroundColor": "#111", "color": COLORS["TEXT"],
                                  "fontWeight": "700", "border": f"1px solid {COLORS['GRID']}"},
                    style_data_conditional=[],
                ),
            ], width=4),
        ]),
        dcc.Graph(id="weather-regional-hdd", style={"marginTop": "16px"}),
        dcc.Graph(id="weather-demand", style={"marginTop": "16px"}),
    ])


def _build_map_figure(records, mode):
    fig = go.Figure()
    if not records:
        fig.update_layout(**plotly_layout(title="No weather data yet — fetching…", height=480))
        return fig
    if mode == "temp":
        zvals = [r["high_f"] for r in records]; cmin, cmid, cmax = 20, 55, 90; cs = "RdBu_r"; cb_title = "°F"
    elif mode == "hdd":
        zvals = [r["hdd"] for r in records]; cmin, cmid, cmax = 0, 20, 50; cs = "Blues"; cb_title = "HDD"
    elif mode == "rev24":
        zvals = [r.get("rev24") if r.get("rev24") is not None else 0.0 for r in records]
        cmin, cmid, cmax = -8, 0, 8; cs = "RdBu"; cb_title = "Δ °F (24h)"
    else:
        zvals = [r["deviation_from_normal"] for r in records]; cmin, cmid, cmax = -15, 0, 15; cs = "RdBu_r"; cb_title = "Dev °F"

    def _fmt_rev(v):
        if v is None: return "—"
        return f"{'▲' if v >= 0 else '▼'} {v:+.1f}"
    customdata = [[
        r["name"], r["high_f"], r["low_f"], r["deviation_from_normal"],
        r["hdd"], r["cdd"],
        _fmt_rev(r.get("rev24")), _fmt_rev(r.get("rev72")),
    ] for r in records]

    fig.add_trace(go.Scattergeo(
        lon=[r["lon"] for r in records],
        lat=[r["lat"] for r in records],
        text=[r["name"] for r in records],
        customdata=customdata,
        marker=dict(
            size=[10 * r["weight"] for r in records],
            color=zvals, colorscale=cs, cmin=cmin, cmid=cmid, cmax=cmax,
            colorbar=dict(title=cb_title, tickfont=dict(color=COLORS["TEXT"]),
                          tickformat=".0f"),
            line=dict(color="white", width=0.5),
        ),
        hovertemplate=("<b>%{customdata[0]}</b><br>"
                       "High: %{customdata[1]:.0f}°F  Low: %{customdata[2]:.0f}°F<br>"
                       "Dev from Normal: %{customdata[3]:+.0f}°F<br>"
                       "HDD: %{customdata[4]:.0f}  CDD: %{customdata[5]:.0f}<br>"
                       "24h Rev: %{customdata[6]}<br>72h Rev: %{customdata[7]}<extra></extra>"),
    ))
    fig.update_geos(scope="usa", bgcolor=COLORS["PANEL"], showland=True,
                    landcolor=COLORS["BG"], showlakes=False, showsubunits=True,
                    subunitcolor=COLORS["GRID"], showcountries=False)
    fig.update_layout(**plotly_layout(title=None, height=480, showlegend=False))
    return fig


def _build_regional_hdd(records):
    fig = go.Figure()
    if not records:
        fig.update_layout(**plotly_layout(title="Regional HDD — 10-day forecast", height=320))
        return fig
    dates = records[0]["dates"][:10]
    region_colors = {"NE": COLORS["BLUE"], "MW": "#cc66ff", "S": COLORS["ORANGE"], "W": "#ffcc33"}
    for region in ["NE", "MW", "S", "W"]:
        region_recs = [r for r in records if r["region"] == region]
        if not region_recs:
            continue
        daily = []
        for d_idx in range(min(10, len(dates))):
            daily.append(sum(r["daily_hdd"][d_idx] * r["weight"] for r in region_recs))
        fig.add_trace(go.Bar(name=region, x=dates[:len(daily)], y=daily,
                              marker_color=region_colors[region]))
    fig.update_layout(**plotly_layout(title="Regional Heating Degree Days — 10-day forecast",
                                      height=320))
    fig.update_layout(barmode="group")
    fig.update_yaxes(title_text="Total HDD (weighted)")
    return fig


def _build_demand_chart(records):
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="National Residential+Commercial Demand Estimate (Bcf/d)",
                                      height=320))
    if not records:
        return fig
    demand = wdata.national_demand_estimate(records, days=7)
    if demand.empty:
        return fig
    month = dt.date.today().month
    lo, hi = config.DEMAND_BAND_5Y.get(month, (0, 100))
    fig.add_trace(go.Scatter(x=demand["date"], y=[hi]*len(demand), mode="lines",
                              line=dict(color=COLORS["GRID"], width=0), showlegend=False))
    fig.add_trace(go.Scatter(x=demand["date"], y=[lo]*len(demand), mode="lines",
                              line=dict(color=COLORS["GRID"], width=0),
                              fill="tonexty", fillcolor="rgba(120,120,120,0.25)",
                              name="5Y seasonal range"))
    fig.add_trace(go.Scatter(x=demand["date"], y=demand["demand_bcf"], mode="lines+markers",
                              line=dict(color=COLORS["BULL"], width=3), name="Est. demand"))
    return fig


def figures_for_snapshot(stores):
    data = (stores or {}).get("weather") or {}
    records = data.get("records") or []
    if not records:
        return []
    return [
        ("Weather — Temperature Map", _build_map_figure(records, "temp")),
        ("Weather — Regional HDD",    _build_regional_hdd(records)),
        ("Weather — National Demand", _build_demand_chart(records)),
    ]


def _prune_snapshots(snapshots, max_age_hours=96, min_gap_minutes=50):
    """Keep snapshots within max_age, at most one per ~hour, capped at 120 entries."""
    if not snapshots:
        return []
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=max_age_hours)
    fresh = []
    for s in snapshots:
        try:
            ts = dt.datetime.fromisoformat(s["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=dt.timezone.utc)
        except Exception:
            continue
        if ts >= cutoff:
            fresh.append((ts, s))
    fresh.sort(key=lambda x: x[0])
    deduped = []
    for ts, s in fresh:
        if deduped:
            prev_ts = dt.datetime.fromisoformat(deduped[-1]["ts"])
            if prev_ts.tzinfo is None:
                prev_ts = prev_ts.replace(tzinfo=dt.timezone.utc)
            if (ts - prev_ts).total_seconds() < min_gap_minutes * 60:
                deduped[-1] = s
                continue
        deduped.append(s)
    return deduped[-120:]


def _find_snapshot_aged(snapshots, target_hours, tolerance_hours=4):
    """Find the snapshot with timestamp closest to ``target_hours`` ago, within tolerance."""
    now = dt.datetime.now(dt.timezone.utc)
    target = now - dt.timedelta(hours=target_hours)
    best = None
    best_dist = float("inf")
    for s in snapshots or []:
        try:
            ts = dt.datetime.fromisoformat(s["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=dt.timezone.utc)
        except Exception:
            continue
        dist = abs((ts - target).total_seconds())
        if dist < best_dist and dist <= tolerance_hours * 3600:
            best = s
            best_dist = dist
    return best


def register_callbacks(app):

    @app.callback(
        Output("weather-data-store", "data"),
        Input("weather-interval", "n_intervals"),
        State("settings-store", "data"),
    )
    def fetch_weather(_, settings):
        enabled_cities = (settings or {}).get("cities")
        cities = [c for c in config.CITIES
                  if enabled_cities is None or c["name"] in enabled_cities]
        raw = wdata.fetch_all_cities(cities)
        if not raw:
            return no_update
        records = wdata.build_city_records(raw, cities)
        if not records:
            return no_update
        for r in records:
            r["rev24"] = r.get("change_24h")
            r["rev72"] = r.get("change_72h")
        return {
            "date": dt.date.today().isoformat(),
            "records": records,
        }

    @app.callback(
        Output("weather-last-update", "children"),
        Input("weather-data-store", "data"),
    )
    def show_last_update(data):
        if not data:
            return ""
        return f"Last update: {data.get('date','—')}"

    @app.callback(
        Output("weather-map", "figure"),
        Input("weather-data-store", "data"),
        Input("weather-mode", "value"),
    )
    def update_map(data, mode):
        return _build_map_figure((data or {}).get("records", []), mode)

    @app.callback(
        Output("weather-revision-table", "data"),
        Output("weather-revision-table", "style_data_conditional"),
        Input("weather-data-store", "data"),
    )
    def update_revision_table(data):
        records = (data or {}).get("records", [])
        if not records:
            return [], []
        def fmt(v):
            return f"{v:+.2f}" if v is not None else "—"
        rows = []
        for r in records:
            v24, v72 = r.get("rev24"), r.get("rev72")
            rows.append({"name": r["name"],
                         "rev24": fmt(v24), "rev72": fmt(v72),
                         "_r24": v24 if v24 is not None else 0.0,
                         "_r72": v72 if v72 is not None else 0.0})
        rows.sort(key=lambda r: abs(r["_r24"]), reverse=True)
        styles = []
        for col, raw in (("rev24", "_r24"), ("rev72", "_r72")):
            for thr, bg in [(4, "#660000"), (2, "#330000"), (-2, "#000033"), (-4, "#000066")]:
                op = ">=" if thr > 0 else "<="
                styles.append({
                    "if": {"column_id": col,
                           "filter_query": f"{{{raw}}} {op} {thr}"},
                    "backgroundColor": bg, "color": "white",
                })
        return rows, styles

    @app.callback(Output("weather-regional-hdd", "figure"),
                  Input("weather-data-store", "data"))
    def update_regional_hdd(data):
        return _build_regional_hdd((data or {}).get("records", []))

    @app.callback(Output("weather-demand", "figure"),
                  Input("weather-data-store", "data"))
    def update_demand(data):
        return _build_demand_chart((data or {}).get("records", []))
