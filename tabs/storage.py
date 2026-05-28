"""Tab 1: EIA weekly storage with bull/bear surprise banner."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html, Input, Output, State, dash_table, no_update
import dash_bootstrap_components as dbc
import pytz

import config
from data import eia, trajectory as traj
from tabs import regional_storage
from utils.theme import COLORS, plotly_layout

NY = pytz.timezone("America/New_York")


_ENLARGE_BTN_STYLE = {
    "position": "absolute", "right": "20px", "top": "8px", "zIndex": 10,
    "fontSize": "11px", "padding": "2px 8px",
}


def _enlargeable_chart(graph_id: str, button_id: str):
    return html.Div([
        dbc.Button("⛶ Enlarge", id=button_id, size="sm", outline=True,
                   color="light", style=_ENLARGE_BTN_STYLE),
        dcc.Graph(id=graph_id),
    ], style={"position": "relative"})


def _chart_modal(modal_id: str, graph_id: str, close_id: str, title: str):
    return dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle(title)),
        dbc.ModalBody(dcc.Graph(
            id=graph_id,
            config={"scrollZoom": True, "displaylogo": False},
            style={"height": "75vh"},
        )),
        dbc.ModalFooter(dbc.Button("Close", id=close_id, color="secondary")),
    ], id=modal_id, size="xl", is_open=False, fullscreen="lg-down")


def layout():
    return html.Div([
        dcc.Interval(id="storage-interval", interval=config.DEFAULT_INTERVALS["storage_ms"]),
        dbc.Alert(id="storage-banner", color="dark",
                  style={"fontSize": "20px", "fontWeight": "700",
                         "textAlign": "center", "padding": "20px"},
                  children="Awaiting EIA data — enter API key in Settings."),
        dbc.Row([
            dbc.Col([
                dbc.Label("Analyst Consensus (Bcf)"),
                dbc.Input(id="storage-consensus", type="number",
                          value=0, step=1, placeholder="e.g. +75"),
            ], width=3),
            dbc.Col(html.Div(id="storage-countdown",
                             style={"textAlign": "right", "color": COLORS["WARN"],
                                    "fontSize": "14px", "paddingTop": "32px"}),
                    width=9),
        ], style={"marginBottom": "16px"}),
        dbc.Row([
            dbc.Col(_enlargeable_chart("storage-52w-chart", "storage-52w-enlarge"), width=6),
            dbc.Col(_enlargeable_chart("storage-seasonal-chart", "storage-seasonal-enlarge"), width=6),
        ]),
        dbc.Row(id="storage-metric-cards", style={"marginTop": "16px"}),
        dcc.Graph(id="storage-cumulative-chart", style={"marginTop": "16px"}),
        _trajectory_section(),
        regional_storage.layout(),
        _chart_modal("storage-52w-modal", "storage-52w-modal-chart",
                     "storage-52w-modal-close", "Weekly Changes — Full History"),
        _chart_modal("storage-seasonal-modal", "storage-seasonal-modal-chart",
                     "storage-seasonal-modal-close",
                     "Working Gas in Storage — Seasonal (All Years)"),
    ])


def _trajectory_section():
    """Full-width End-of-Season Trajectory Model panel below the cumulative chart."""
    return html.Div([
        dcc.Interval(id="trajectory-interval",
                     interval=config.DEFAULT_INTERVALS["trajectory_ms"]),
        html.Div([
            html.H4("End-of-Season Storage Trajectory Model",
                    style={"color": COLORS["TEXT"], "marginBottom": "4px",
                           "marginTop": "32px"}),
            html.Div(id="trajectory-last-updated",
                     style={"fontSize": "12px", "color": COLORS["MUTED"]}),
        ]),
        dcc.Graph(id="trajectory-chart", style={"height": "560px"}),
        dbc.Card(dbc.CardBody([
            html.H5("Scenario Assumptions",
                    style={"color": COLORS["TEXT"], "marginBottom": "12px"}),
            dbc.Row(id="trajectory-assumptions-cards"),
            html.Div(style={"marginTop": "16px"}, children=[
                dbc.Button("Show Detailed Assumptions",
                           id="trajectory-details-toggle", size="sm",
                           outline=True, color="light"),
                dbc.Collapse(
                    html.Div(id="trajectory-details-table",
                             style={"marginTop": "12px"}),
                    id="trajectory-details-collapse", is_open=False,
                ),
            ]),
        ]), className="metric-card", style={"marginTop": "16px"}),
    ])


def _metric_card(title, value, sub=None, color=None):
    color = color or COLORS["TEXT"]
    return dbc.Col(dbc.Card(dbc.CardBody([
        html.Div(title, className="metric-title"),
        html.Div(value, className="metric-value", style={"color": color}),
        html.Div(sub or "", style={"fontSize": "12px", "color": COLORS["MUTED"]}),
    ]), className="metric-card"), width=3)


def figures_for_snapshot(stores):
    """Return chart figures for PDF snapshot."""
    data = (stores or {}).get("storage") or {}
    if not data:
        return []
    df = pd.DataFrame(data.get("series", []))
    if df.empty:
        return []
    df["period"] = pd.to_datetime(df["period"])
    df = df.set_index("period")
    figs = []
    figs.append(("Storage — last 52 weekly changes",
                 _build_52w_figure(df["value_bcf"].diff().dropna().tail(52), 0)))
    bands = eia.compute_seasonal_bands(df)
    figs.append(("Storage — seasonal range", _build_seasonal_figure(df, bands)))
    return figs


def _build_52w_figure(changes: pd.Series, consensus: float) -> go.Figure:
    fig = go.Figure()
    colors_arr = [COLORS["MUTED"]] * len(changes)
    if len(colors_arr):
        colors_arr[-1] = COLORS["TEXT"]
    fig.add_trace(go.Bar(
        x=[d.strftime("%b %d") for d in changes.index],
        y=changes.values, marker_color=colors_arr,
        name="Weekly Δ (Bcf)",
    ))
    if consensus is not None:
        fig.add_hline(y=consensus, line_dash="dash", line_color=COLORS["WARN"],
                      annotation_text=f"Consensus {consensus:+.0f}", annotation_position="top right")
    fig.add_hline(y=0, line_color=COLORS["GRID"])
    fig.update_layout(**plotly_layout(title="Last 52 Weekly Changes (Bcf)",
                                     height=380, showlegend=False))
    return fig


def _build_52w_expanded_figure(changes: pd.Series, consensus: float) -> go.Figure:
    """Full-history weekly-changes bar chart with rangeslider + year selector.

    Bars colored by sign so injections/withdrawals are readable across hundreds
    of bars. Default visible window = last 52 weeks; user can pan/zoom to all.
    """
    fig = go.Figure()
    if changes.empty:
        fig.update_layout(**plotly_layout(title="Weekly Changes (Bcf) — no data", height=600))
        return fig
    colors_arr = [COLORS["BULL"] if v >= 0 else COLORS["BEAR"] for v in changes.values]
    fig.add_trace(go.Bar(
        x=changes.index, y=changes.values,
        marker_color=colors_arr,
        hovertemplate="%{x|%b %d, %Y}<br>%{y:+.0f} Bcf<extra></extra>",
        name="Weekly Δ (Bcf)",
    ))
    if consensus is not None:
        fig.add_hline(y=consensus, line_dash="dash", line_color=COLORS["WARN"],
                      annotation_text=f"Consensus {consensus:+.0f}",
                      annotation_position="top right")
    fig.add_hline(y=0, line_color=COLORS["GRID"])
    initial_start = changes.index[-min(52, len(changes))]
    initial_end = changes.index[-1]
    fig.update_layout(**plotly_layout(
        title=f"Weekly Changes — {len(changes):,} weeks of history",
        showlegend=False,
    ))
    fig.update_xaxes(
        type="date",
        range=[initial_start, initial_end],
        rangeslider=dict(visible=True, thickness=0.06,
                         bgcolor=COLORS["BG"], bordercolor=COLORS["GRID"]),
        rangeselector=dict(
            buttons=[
                dict(count=6,  label="6m", step="month", stepmode="backward"),
                dict(count=1,  label="1y", step="year",  stepmode="backward"),
                dict(count=3,  label="3y", step="year",  stepmode="backward"),
                dict(count=5,  label="5y", step="year",  stepmode="backward"),
                dict(step="all", label="All"),
            ],
            bgcolor=COLORS["PANEL"], activecolor=COLORS["BLUE"],
            font=dict(color=COLORS["TEXT"]),
        ),
    )
    return fig


def _build_seasonal_expanded_figure(df: pd.DataFrame, bands: pd.DataFrame) -> go.Figure:
    """Seasonal chart with every available year overlaid as its own line."""
    fig = go.Figure()
    if df.empty:
        fig.update_layout(**plotly_layout(title="Seasonal — no data", height=600))
        return fig
    if not bands.empty:
        weeks = bands.index.tolist()
        fig.add_trace(go.Scatter(x=weeks, y=bands["max_5y"], mode="lines",
                                 line=dict(color=COLORS["GRID"], width=0),
                                 name="5y max", showlegend=False))
        fig.add_trace(go.Scatter(x=weeks, y=bands["min_5y"], mode="lines",
                                 line=dict(color=COLORS["GRID"], width=0),
                                 fill="tonexty", fillcolor="rgba(120,120,120,0.25)",
                                 name="5y range"))
        fig.add_trace(go.Scatter(x=weeks, y=bands["avg_5y"], mode="lines",
                                 line=dict(color=COLORS["TEXT"], dash="dash", width=2),
                                 name="5y avg"))

    years = sorted(df.index.year.unique())
    current_year = max(years)
    year_palette = [COLORS["MUTED"], COLORS["BLUE"], COLORS["ORANGE"],
                    COLORS["WARN"], COLORS["BEAR"], "#9966ff", "#33cccc", "#cc66ff"]
    for i, yr in enumerate(years):
        d = df[df.index.year == yr].copy()
        if d.empty:
            continue
        d["week"] = d.index.isocalendar().week
        d = d.sort_values("week")
        is_current = (yr == current_year)
        fig.add_trace(go.Scatter(
            x=d["week"], y=d["value_bcf"], mode="lines+markers" if is_current else "lines",
            line=dict(
                color=COLORS["BULL"] if is_current else year_palette[i % len(year_palette)],
                width=3 if is_current else 1.5,
                dash="solid" if is_current else "dot",
            ),
            marker=dict(size=4) if is_current else None,
            opacity=1.0 if is_current else 0.7,
            name=str(yr),
            hovertemplate=f"{yr} • week %{{x}}<br>%{{y:,.0f}} Bcf<extra></extra>",
        ))
    fig.update_layout(**plotly_layout(
        title=f"Working Gas — Seasonal ({len(years)} years overlaid)",
    ))
    fig.update_xaxes(title_text="Week of Year", dtick=4, range=[0, 53])
    fig.update_yaxes(title_text="Bcf")
    return fig


def _build_seasonal_figure(df: pd.DataFrame, bands: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not bands.empty:
        weeks = bands.index.tolist()
        fig.add_trace(go.Scatter(x=weeks, y=bands["max_5y"], mode="lines",
                                  line=dict(color=COLORS["GRID"], width=0),
                                  name="5y max", showlegend=False))
        fig.add_trace(go.Scatter(x=weeks, y=bands["min_5y"], mode="lines",
                                  line=dict(color=COLORS["GRID"], width=0),
                                  fill="tonexty", fillcolor="rgba(120,120,120,0.25)",
                                  name="5y range"))
        fig.add_trace(go.Scatter(x=weeks, y=bands["avg_5y"], mode="lines",
                                  line=dict(color=COLORS["TEXT"], dash="dash"),
                                  name="5y avg"))
    if not df.empty:
        current_year = df.index.year.max()
        cur = df[df.index.year == current_year].copy()
        cur["week"] = cur.index.isocalendar().week
        fig.add_trace(go.Scatter(x=cur["week"], y=cur["value_bcf"], mode="lines",
                                  line=dict(color=COLORS["BULL"], width=3),
                                  name=str(current_year)))
        prior = df[df.index.year == current_year - 1].copy()
        prior["week"] = prior.index.isocalendar().week
        fig.add_trace(go.Scatter(x=prior["week"], y=prior["value_bcf"], mode="lines",
                                  line=dict(color=COLORS["ORANGE"], dash="dot"),
                                  name=str(current_year - 1)))
    fig.update_layout(**plotly_layout(title="Working Gas in Storage — Seasonal", height=380))
    fig.update_xaxes(title_text="Week of Year")
    fig.update_yaxes(title_text="Bcf")
    return fig


def _build_trajectory_figure(payload: dict | None) -> go.Figure:
    """Render the end-of-season trajectory chart from a trajectory-store payload."""
    fig = go.Figure()
    if not payload or not payload.get("ready"):
        fig.update_layout(**plotly_layout(
            title="End-of-Season Trajectory — awaiting data",
            height=520, showlegend=False,
        ))
        return fig

    scen = payload["scenarios"]
    targets = payload["targets"]
    if not scen["dates"]:
        fig.update_layout(**plotly_layout(
            title="End-of-Season Trajectory — no scenario data",
            height=520, showlegend=False,
        ))
        return fig

    dates = pd.to_datetime(scen["dates"])
    today = pd.Timestamp(payload["current_date"])
    target_dt = pd.Timestamp(payload["target_date"])
    season = payload.get("season", "injection")

    # Vertical zones: blue tint for 10-day weather window, grey beyond.
    ten_day = today + pd.Timedelta(days=10)
    fig.add_vrect(x0=today, x1=ten_day, fillcolor=COLORS["BLUE"],
                  opacity=0.06, line_width=0, layer="below")
    fig.add_vrect(x0=ten_day, x1=target_dt, fillcolor=COLORS["MUTED"],
                  opacity=0.05, line_width=0, layer="below")

    # Confidence band (drawn first so scenario lines sit on top).
    fig.add_trace(go.Scatter(
        x=dates, y=scen["band_high"], mode="lines",
        line=dict(width=0), showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=scen["band_low"], mode="lines", line=dict(width=0),
        fill="tonexty", fillcolor="rgba(120,120,120,0.18)",
        name="Base ±σ band", hoverinfo="skip",
    ))

    # Prior year reference line (thin dotted grey).
    py = payload.get("prior_year_path") or {}
    if py.get("weeks") and py.get("values"):
        cy = today.year
        py_dates = []
        for wk in py["weeks"]:
            try:
                py_dates.append(pd.Timestamp.fromisocalendar(cy, int(wk), 4))
            except (ValueError, AttributeError):
                py_dates.append(pd.NaT)
        fig.add_trace(go.Scatter(
            x=py_dates, y=py["values"], mode="lines",
            line=dict(color=COLORS["MUTED"], width=1, dash="dot"),
            name=f"{cy-1} reference", opacity=0.6,
            hovertemplate=f"{cy-1} week %{{x|%b %d}}<br>%{{y:,.0f}} Bcf<extra></extra>",
        ))

    # Historical actual YTD path (thick yellow).
    ytd = payload.get("historical_actual_ytd") or {}
    if ytd.get("dates") and ytd.get("values"):
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(ytd["dates"]), y=ytd["values"], mode="lines",
            line=dict(color=COLORS["WARN"], width=3),
            name=f"Actual {today.year} YTD",
            hovertemplate="%{x|%b %d}<br>Actual: %{y:,.0f} Bcf<extra></extra>",
        ))

    # Three scenario lines.
    changes = scen.get("weekly_changes", [])
    custom = [
        [c["baseline"], c["prod"], c["lng"], c.get("demand", 0), c["weather"], c["std"]]
        for c in changes
    ]
    scenario_hover = (
        "<b>%{x|%b %d, %Y}</b><br>"
        "Projected: %{y:,.0f} Bcf<br>"
        "Baseline: %{customdata[0]:+.1f}  Prod: %{customdata[1]:+.1f}  "
        "LNG: %{customdata[2]:+.1f}  Demand: %{customdata[3]:+.1f}  "
        "Wx: %{customdata[4]:+.1f}<br>"
        "±σ at horizon: %{customdata[5]:.1f}"
        "<extra>%{fullData.name}</extra>"
    )
    fig.add_trace(go.Scatter(
        x=dates, y=scen["base"], mode="lines+markers",
        line=dict(color=COLORS["TEXT"], width=3),
        marker=dict(size=6),
        name="Base case", customdata=custom, hovertemplate=scenario_hover,
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=scen["bull"], mode="lines",
        line=dict(color=COLORS["BEAR"], width=2.5),
        name="Bull (price)", customdata=custom, hovertemplate=scenario_hover,
    ))
    fig.add_trace(go.Scatter(
        x=dates, y=scen["bear"], mode="lines",
        line=dict(color=COLORS["BULL"], width=2.5),
        name="Bear (price)", customdata=custom, hovertemplate=scenario_hover,
    ))

    # Horizontal reference lines.
    avg = targets.get("target_avg")
    mn = targets.get("target_min"); mx = targets.get("target_max")
    minc = targets.get("min_comfortable")
    if avg is not None:
        fig.add_hline(y=avg, line_dash="dash", line_color=COLORS["TEXT"],
                      annotation_text=f"5y avg {avg:,.0f}",
                      annotation_position="top left",
                      annotation=dict(font=dict(color=COLORS["TEXT"], size=11)))
    if mx is not None:
        fig.add_hline(y=mx, line_dash="dot", line_color=COLORS["MUTED"],
                      annotation_text=f"5y max {mx:,.0f}",
                      annotation_position="top left",
                      annotation=dict(font=dict(color=COLORS["MUTED"], size=10)))
    if mn is not None:
        fig.add_hline(y=mn, line_dash="dot", line_color=COLORS["MUTED"],
                      annotation_text=f"5y min {mn:,.0f}",
                      annotation_position="bottom left",
                      annotation=dict(font=dict(color=COLORS["MUTED"], size=10)))
    if minc is not None:
        fig.add_hline(y=minc, line_dash="dash", line_color=COLORS["ORANGE"],
                      annotation_text=f"Min comfortable {minc:,.0f}",
                      annotation_position="bottom right",
                      annotation=dict(font=dict(color=COLORS["ORANGE"], size=11)))

    # End-point annotations: large bold scenario labels with surplus/deficit.
    base_eos = scen["base"][-1]; bull_eos = scen["bull"][-1]; bear_eos = scen["bear"][-1]
    end_dt = dates[-1]
    def _surplus_text(value):
        if avg is None: return ""
        diff = value - avg
        return f" ({diff:+,.0f} {'surplus' if diff >= 0 else 'deficit'})"

    fig.add_annotation(x=end_dt, y=base_eos, xshift=70,
                       text=f"<b>BASE</b><br>{base_eos:,.0f}{_surplus_text(base_eos)}",
                       showarrow=False, font=dict(color=COLORS["TEXT"], size=12),
                       align="left", xanchor="left")
    fig.add_annotation(x=end_dt, y=bull_eos, xshift=70,
                       text=f"<b>BULL</b><br>{bull_eos:,.0f}{_surplus_text(bull_eos)}",
                       showarrow=False, font=dict(color=COLORS["BEAR"], size=12),
                       align="left", xanchor="left")
    fig.add_annotation(x=end_dt, y=bear_eos, xshift=70,
                       text=f"<b>BEAR</b><br>{bear_eos:,.0f}{_surplus_text(bear_eos)}",
                       showarrow=False, font=dict(color=COLORS["BULL"], size=12),
                       align="left", xanchor="left")

    title = (f"End-of-Season Trajectory — {season.title()} season  "
             f"(target {target_dt.strftime('%b %d, %Y')})")
    fig.update_layout(**plotly_layout(title=title, height=560, showlegend=True))
    fig.update_xaxes(title_text="", range=[today - pd.Timedelta(days=14), target_dt + pd.Timedelta(days=20)])
    fig.update_yaxes(title_text="Working Gas (Bcf)")
    return fig


def _assumption_card(title, value, sub=None, color=None):
    color = color or COLORS["TEXT"]
    return dbc.Col(dbc.Card(dbc.CardBody([
        html.Div(title, className="metric-title"),
        html.Div(value, className="metric-value", style={"color": color, "fontSize": "20px"}),
        html.Div(sub or "", style={"fontSize": "11px", "color": COLORS["MUTED"]}),
    ]), className="metric-card"), xs=12, sm=6, md=4, lg=3)


def _build_assumption_cards(payload: dict | None):
    if not payload or not payload.get("ready"):
        return [_assumption_card("Awaiting data", "—")]
    cur4 = payload.get("current_4wk") or {}
    adj = payload.get("adjustments") or {}
    prod_color   = COLORS["BULL"] if adj.get("production_bcfwk", 0) > 0 else COLORS["BEAR"]
    lng_color    = COLORS["BULL"] if adj.get("lng_bcfwk", 0) > 0 else COLORS["BEAR"]
    demand_color = COLORS["BULL"] if adj.get("demand_bcfwk", 0) > 0 else COLORS["BEAR"]
    wx_color     = COLORS["BULL"] if adj.get("weather_first_week_bcf", 0) > 0 else COLORS["BEAR"]

    required = payload.get("required_pace_bcfwk")
    current_pace = (cur4 or {}).get("injection_pace")
    pace_color = COLORS["MUTED"]
    pace_sub = "—"
    if required is not None and current_pace is not None:
        pace_color = COLORS["BULL"] if current_pace >= required else COLORS["BEAR"]
        gap = current_pace - required
        pace_sub = f"current 4-wk pace {current_pace:+.0f} ({gap:+.0f} vs required)"

    momentum = payload.get("momentum_bcfwk")
    momentum_arrow = "—"
    momentum_color = COLORS["MUTED"]
    if momentum is not None:
        if momentum > 0:
            momentum_arrow = f"▲ {momentum:+.1f} Bcf/wk"
            momentum_color = COLORS["BULL"]
        elif momentum < 0:
            momentum_arrow = f"▼ {momentum:+.1f} Bcf/wk"
            momentum_color = COLORS["BEAR"]

    return [
        _assumption_card("Current Storage",
                         f"{payload.get('current_storage', 0):,.0f} Bcf",
                         f"as of {payload.get('current_date','—')}"),
        _assumption_card("Weeks Remaining",
                         f"{payload.get('weeks_remaining', 0)}",
                         f"to {payload.get('target_date','—')}"),
        _assumption_card("Production (12mo avg)",
                         f"{cur4.get('production_bcfd','—')} Bcf/d" if cur4.get("production_bcfd") else "—",
                         f"vs 3y prior: adj {adj.get('production_bcfwk',0):+.1f} Bcf/wk",
                         prod_color),
        _assumption_card("LNG Exports (12mo avg)",
                         f"{cur4.get('lng_bcfd','—')} Bcf/d" if cur4.get("lng_bcfd") else "—",
                         f"vs 3y prior: adj {adj.get('lng_bcfwk',0):+.1f} Bcf/wk",
                         lng_color),
        _assumption_card("Domestic Demand (12mo avg)",
                         f"{cur4.get('demand_bcfd','—')} Bcf/d" if cur4.get("demand_bcfd") else "—",
                         f"vs 3y prior: adj {adj.get('demand_bcfwk',0):+.1f} Bcf/wk",
                         demand_color),
        _assumption_card("Weather (next week)",
                         f"{adj.get('weather_first_week_bcf',0):+.1f} Bcf",
                         "decay 15%/wk beyond wk 2, zero at wk 6",
                         wx_color),
        _assumption_card("Required Weekly Pace",
                         f"{required:+.0f} Bcf/wk" if required is not None else "—",
                         pace_sub, pace_color),
        _assumption_card("Momentum (4w vs 4w prior)",
                         momentum_arrow, "injection-pace rate of change",
                         momentum_color),
    ]


def _build_details_table(payload: dict | None):
    if not payload or not payload.get("ready"):
        return html.Div("Awaiting trajectory data.", style={"color": COLORS["MUTED"]})
    scen = payload.get("scenarios", {})
    weekly = scen.get("weekly_changes", [])
    base_path = scen.get("base", [])
    if not weekly:
        return html.Div("No scenario weeks computed.", style={"color": COLORS["MUTED"]})
    rows = []
    for change, cum in zip(weekly, base_path):
        demand = change.get("demand", 0)
        total = (change["baseline"] + change["prod"] + change["lng"]
                 + demand + change["weather"])
        rows.append({
            "Week":       pd.Timestamp(change["week"]).strftime("%Y-%m-%d"),
            "Baseline":   f"{change['baseline']:+.1f}",
            "Prod Adj":   f"{change['prod']:+.1f}",
            "LNG Adj":    f"{change['lng']:+.1f}",
            "Demand Adj": f"{demand:+.1f}",
            "Wx Adj":     f"{change['weather']:+.1f}",
            "Total Δ":    f"{total:+.1f}",
            "Storage":    f"{cum:,.0f}",
            "_total":     total,
        })
    columns = [{"name": k, "id": k} for k in
               ("Week", "Baseline", "Prod Adj", "LNG Adj", "Demand Adj",
                "Wx Adj", "Total Δ", "Storage")]
    return dash_table.DataTable(
        data=rows, columns=columns,
        style_as_list_view=True,
        style_header={"backgroundColor": COLORS["PANEL"], "color": COLORS["TEXT"],
                      "fontWeight": "700", "border": f"1px solid {COLORS['GRID']}"},
        style_cell={"backgroundColor": COLORS["BG"], "color": COLORS["TEXT"],
                    "border": f"1px solid {COLORS['GRID']}", "fontSize": "12px",
                    "padding": "6px 10px", "textAlign": "right"},
        style_cell_conditional=[
            {"if": {"column_id": "Week"}, "textAlign": "left"},
        ],
        style_data_conditional=[
            {"if": {"filter_query": "{_total} > 0", "column_id": "Total Δ"},
             "color": COLORS["BULL"]},
            {"if": {"filter_query": "{_total} < 0", "column_id": "Total Δ"},
             "color": COLORS["BEAR"]},
            {"if": {"filter_query": "{_total} > 50"},
             "backgroundColor": "rgba(0,255,136,0.08)"},
            {"if": {"filter_query": "{_total} < 0"},
             "backgroundColor": "rgba(255,51,51,0.08)"},
        ],
        page_size=20,
    )


def register_callbacks(app):

    @app.callback(
        Output("storage-history-store", "data"),
        Input("storage-interval", "n_intervals"),
        State("settings-store", "data"),
    )
    def fetch_storage(_, settings):
        api_key = (settings or {}).get("eia_key") or config.EIA_API_KEY_FALLBACK
        df = eia.fetch_storage_weekly(api_key)
        if df.empty:
            return no_update
        bands = eia.compute_seasonal_bands(df)
        latest_value = float(df["value_bcf"].iloc[-1])
        latest_week = int(df.index[-1].isocalendar().week)
        avg_5y = float(bands.loc[latest_week, "avg_5y"]) if (not bands.empty and latest_week in bands.index) else None
        max_5y = float(bands.loc[latest_week, "max_5y"]) if (not bands.empty and latest_week in bands.index) else None
        min_5y = float(bands.loc[latest_week, "min_5y"]) if (not bands.empty and latest_week in bands.index) else None
        deviation = (latest_value - avg_5y) if avg_5y is not None else None
        weekly_change = float(df["value_bcf"].diff().iloc[-1]) if len(df) > 1 else None
        series = df.reset_index().to_dict("records")
        for r in series:
            r["period"] = r["period"].isoformat() if hasattr(r["period"], "isoformat") else str(r["period"])
        return {
            "series": series,
            "latest_value": latest_value,
            "latest_date": df.index[-1].isoformat(),
            "weekly_change": weekly_change,
            "avg_5y": avg_5y, "max_5y": max_5y, "min_5y": min_5y,
            "deviation": deviation,
        }

    @app.callback(
        Output("storage-banner", "children"),
        Output("storage-banner", "style"),
        Input("storage-history-store", "data"),
        Input("storage-consensus", "value"),
    )
    def update_banner(data, consensus):
        base_style = {"fontSize": "20px", "fontWeight": "700",
                      "textAlign": "center", "padding": "20px"}
        if not data or data.get("weekly_change") is None:
            return "Awaiting EIA data — enter API key in Settings.", {**base_style, "backgroundColor": COLORS["PANEL"], "color": COLORS["MUTED"]}
        actual = data["weekly_change"]
        cons = float(consensus or 0)
        surprise = actual - cons
        bullish = actual < cons
        color = COLORS["BEAR"] if bullish else COLORS["BULL"]
        label = "BULLISH SURPRISE" if bullish else "BEARISH SURPRISE"
        text = (f"WEEKLY CHANGE: {actual:+.0f} Bcf  •  CONSENSUS: {cons:+.0f}  •  "
                f"SURPRISE: {surprise:+.0f}  •  {label}")
        return text, {**base_style, "backgroundColor": color, "color": "#000"}

    @app.callback(
        Output("storage-countdown", "children"),
        Input("clock-interval", "n_intervals"),
    )
    def countdown(_):
        now = dt.datetime.now(NY)
        target = eia.next_eia_release(now)
        delta = target - now
        days = delta.days
        hours, rem = divmod(delta.seconds, 3600)
        mins, secs = divmod(rem, 60)
        return f"Next EIA release: {target.strftime('%a %b %d %I:%M%p ET')}  •  in {days}d {hours:02d}h {mins:02d}m {secs:02d}s"

    @app.callback(
        Output("storage-52w-chart", "figure"),
        Input("storage-history-store", "data"),
        Input("storage-consensus", "value"),
    )
    def update_52w_chart(data, consensus):
        if not data:
            return go.Figure(layout=plotly_layout(title="Last 52 Weekly Changes (Bcf)", height=380))
        df = pd.DataFrame(data["series"])
        df["period"] = pd.to_datetime(df["period"])
        df = df.set_index("period")
        return _build_52w_figure(df["value_bcf"].diff().dropna().tail(52), float(consensus or 0))

    @app.callback(
        Output("storage-seasonal-chart", "figure"),
        Input("storage-history-store", "data"),
    )
    def update_seasonal_chart(data):
        if not data:
            return go.Figure(layout=plotly_layout(title="Working Gas — Seasonal", height=380))
        df = pd.DataFrame(data["series"])
        df["period"] = pd.to_datetime(df["period"])
        df = df.set_index("period")
        bands = eia.compute_seasonal_bands(df)
        return _build_seasonal_figure(df, bands)

    @app.callback(
        Output("storage-metric-cards", "children"),
        Input("storage-history-store", "data"),
    )
    def update_metrics(data):
        if not data:
            return []
        latest = data.get("latest_value", 0)
        avg = data.get("avg_5y"); mx = data.get("max_5y"); mn = data.get("min_5y")
        dev = data.get("deviation")
        def fmt_dev(v, base):
            if v is None or base in (None, 0): return ("—", "—")
            return (f"{v:+,.0f} Bcf", f"{(v/base)*100:+.1f}%")
        dev_text, dev_pct = fmt_dev(dev, avg)
        dev_max, _ = fmt_dev(latest - mx if mx is not None else None, mx)
        dev_min, _ = fmt_dev(latest - mn if mn is not None else None, mn)
        def color_for(v):
            if v is None: return COLORS["MUTED"]
            return COLORS["BULL"] if v > 0 else (COLORS["BEAR"] if v < 0 else COLORS["TEXT"])
        return [
            _metric_card("Current Storage", f"{latest:,.0f} Bcf"),
            _metric_card("vs 5Y Avg", dev_text, dev_pct, color_for(dev)),
            _metric_card("vs 5Y Max", dev_max, None,
                         color_for(latest - mx if mx is not None else None)),
            _metric_card("vs 5Y Min", dev_min, None,
                         color_for(latest - mn if mn is not None else None)),
        ]

    @app.callback(
        Output("storage-52w-modal", "is_open"),
        Input("storage-52w-enlarge", "n_clicks"),
        Input("storage-52w-modal-close", "n_clicks"),
        State("storage-52w-modal", "is_open"),
        prevent_initial_call=True,
    )
    def toggle_52w_modal(open_click, close_click, is_open):
        return not is_open

    @app.callback(
        Output("storage-seasonal-modal", "is_open"),
        Input("storage-seasonal-enlarge", "n_clicks"),
        Input("storage-seasonal-modal-close", "n_clicks"),
        State("storage-seasonal-modal", "is_open"),
        prevent_initial_call=True,
    )
    def toggle_seasonal_modal(open_click, close_click, is_open):
        return not is_open

    @app.callback(
        Output("storage-52w-modal-chart", "figure"),
        Input("storage-history-store", "data"),
        Input("storage-consensus", "value"),
    )
    def update_52w_modal_chart(data, consensus):
        if not data:
            return go.Figure(layout=plotly_layout(title="Weekly Changes — full history"))
        df = pd.DataFrame(data["series"])
        df["period"] = pd.to_datetime(df["period"])
        df = df.set_index("period")
        return _build_52w_expanded_figure(df["value_bcf"].diff().dropna(), float(consensus or 0))

    @app.callback(
        Output("storage-seasonal-modal-chart", "figure"),
        Input("storage-history-store", "data"),
    )
    def update_seasonal_modal_chart(data):
        if not data:
            return go.Figure(layout=plotly_layout(title="Seasonal — all years"))
        df = pd.DataFrame(data["series"])
        df["period"] = pd.to_datetime(df["period"])
        df = df.set_index("period")
        bands = eia.compute_seasonal_bands(df)
        return _build_seasonal_expanded_figure(df, bands)

    @app.callback(
        Output("storage-cumulative-chart", "figure"),
        Input("storage-history-store", "data"),
    )
    def update_cumulative(data):
        fig = go.Figure()
        fig.update_layout(**plotly_layout(
            title="Cumulative Net Injection vs 5Y Avg (since Apr 1)",
            height=360, showlegend=True))
        if not data:
            return fig
        df = pd.DataFrame(data["series"])
        df["period"] = pd.to_datetime(df["period"])
        df = df.set_index("period")
        bands = eia.compute_seasonal_bands(df)
        paths = eia.cumulative_paths(df, bands)
        if paths.empty:
            return fig

        # 5y average reference line — drawn first so the actual line sits on top.
        fig.add_trace(go.Scatter(
            x=paths.index, y=paths["avg_5y"], mode="lines",
            line=dict(color=COLORS["MUTED"], dash="dash", width=2),
            name="5y avg path",
            hovertemplate="%{x|%b %d}<br>5y avg: %{y:+,.0f} Bcf<extra></extra>",
        ))

        # Shade the gap: split into above/below segments so color reflects sign.
        actual = paths["actual"].values
        avg    = paths["avg_5y"].values
        above = paths["actual"].where(paths["surplus"] >= 0, paths["avg_5y"])
        below = paths["actual"].where(paths["surplus"] <  0, paths["avg_5y"])
        fig.add_trace(go.Scatter(
            x=paths.index, y=above, mode="lines",
            line=dict(width=0), fill="tonexty",
            fillcolor="rgba(0,255,136,0.28)", name="Surplus vs 5y",
            hoverinfo="skip",
        ))
        # Re-add 5y avg as anchor for the deficit fill (Plotly fills relative to prior trace).
        fig.add_trace(go.Scatter(
            x=paths.index, y=paths["avg_5y"], mode="lines",
            line=dict(width=0), showlegend=False, hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=paths.index, y=below, mode="lines",
            line=dict(width=0), fill="tonexty",
            fillcolor="rgba(255,51,51,0.28)", name="Deficit vs 5y",
            hoverinfo="skip",
        ))

        # Actual cumulative injection — the headline line.
        fig.add_trace(go.Scatter(
            x=paths.index, y=paths["actual"], mode="lines+markers",
            line=dict(color=COLORS["BULL"], width=2.5),
            marker=dict(size=4),
            name=f"Actual ({df.index.year.max()})",
            hovertemplate="%{x|%b %d}<br>Actual: %{y:+,.0f} Bcf<extra></extra>",
        ))
        fig.add_hline(y=0, line_color=COLORS["GRID"])
        fig.update_yaxes(title_text="Cumulative Δ since Apr 1 (Bcf)")
        return fig

    # ── End-of-Season Trajectory Model ─────────────────────────────────────────

    @app.callback(
        Output("trajectory-store", "data"),
        Output("trajectory-last-calculated", "data"),
        Input("storage-history-store", "data"),
        Input("weather-data-store", "data"),
        Input("trajectory-interval", "n_intervals"),
        State("settings-store", "data"),
    )
    def compute_trajectory_cb(storage_data, weather_data, _n, settings):
        payload = traj.compute_trajectory(storage_data, weather_data, settings)
        return payload, payload.get("calculated_at") if payload.get("ready") else None

    @app.callback(
        Output("trajectory-chart", "figure"),
        Input("trajectory-store", "data"),
    )
    def update_trajectory_chart(payload):
        return _build_trajectory_figure(payload)

    @app.callback(
        Output("trajectory-assumptions-cards", "children"),
        Output("trajectory-last-updated", "children"),
        Input("trajectory-store", "data"),
    )
    def update_assumptions_panel(payload):
        cards = _build_assumption_cards(payload)
        last_updated = (payload or {}).get("calculated_at")
        if last_updated:
            ts = pd.to_datetime(last_updated).strftime("%Y-%m-%d %H:%M:%S")
            stamp = f"Model last recalculated: {ts}"
        else:
            stamp = "Model awaiting first calculation"
        return cards, stamp

    @app.callback(
        Output("trajectory-details-table", "children"),
        Input("trajectory-store", "data"),
    )
    def update_details_table(payload):
        return _build_details_table(payload)

    @app.callback(
        Output("trajectory-details-collapse", "is_open"),
        Output("trajectory-details-toggle", "children"),
        Input("trajectory-details-toggle", "n_clicks"),
        State("trajectory-details-collapse", "is_open"),
        prevent_initial_call=True,
    )
    def toggle_details_collapse(_, is_open):
        new_open = not is_open
        return new_open, ("Hide Detailed Assumptions" if new_open
                          else "Show Detailed Assumptions")
