"""Tab 4: CFTC Commitments of Traders positioning analytics."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html, Input, Output, State, no_update
import dash_bootstrap_components as dbc
import pytz

import config
from data import cftc, futures
from utils.theme import COLORS, plotly_layout

NY = pytz.timezone("America/New_York")


def layout():
    return html.Div([
        dcc.Interval(id="cftc-interval", interval=config.DEFAULT_INTERVALS["cftc_ms"]),
        dbc.Row([
            dbc.Col(dcc.Graph(id="cftc-mm-chart"), width=8),
            dbc.Col(dcc.Graph(id="cftc-percentile-gauge"), width=4),
        ]),
        dbc.Row([
            dbc.Col(dcc.Graph(id="cftc-heatmap"), width=8),
            dbc.Col(dcc.Graph(id="cftc-momentum"), width=4),
        ], style={"marginTop": "8px"}),
        dcc.Graph(id="cftc-three-category", style={"marginTop": "8px"}),
        dcc.Graph(id="cftc-historical-signal", style={"marginTop": "8px"}),
    ])


def _df_from_store(data):
    if not data or not data.get("records"):
        return pd.DataFrame()
    df = pd.DataFrame(data["records"])
    if "report_date" in df.columns:
        df["report_date"] = pd.to_datetime(df["report_date"])
        df = df.set_index("report_date").sort_index()
    return df


def _build_mm_chart(df):
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="Managed Money Long/Short + Net (52 weeks)",
                                      height=380))
    if df.empty:
        return fig
    last = df.tail(52)
    fig.add_trace(go.Bar(x=last.index, y=last["mm_long"], name="MM Longs",
                          marker_color=COLORS["BULL"]))
    fig.add_trace(go.Bar(x=last.index, y=-last["mm_short"], name="MM Shorts",
                          marker_color=COLORS["BEAR"]))
    fig.add_trace(go.Scatter(x=last.index, y=last["mm_net"], mode="lines+markers",
                              line=dict(color=COLORS["TEXT"], width=3),
                              name="Net MM", yaxis="y"))
    fig.update_layout(barmode="relative")
    return fig


def _build_percentile_gauge(df):
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="Net MM Positioning — 3Y Percentile",
                                      height=340, showlegend=False))
    if df.empty:
        return fig
    series = df["mm_net"].tail(156)
    if series.empty:
        return fig
    current = float(series.iloc[-1])
    prev = float(series.iloc[-2]) if len(series) > 1 else current
    rank = cftc.percentile_rank(series, current)
    prev_rank = cftc.percentile_rank(series, prev)
    fig.add_trace(go.Indicator(
        mode="gauge+number+delta",
        value=rank, delta={"reference": prev_rank, "valueformat": "+.1f"},
        number={"suffix": "%", "font": {"color": COLORS["TEXT"]}},
        title={"text": "Net MM Rank", "font": {"color": COLORS["TEXT"]}},
        gauge={
            "axis": {"range": [0, 100], "tickfont": {"color": COLORS["TEXT"]}},
            "bar": {"color": COLORS["TEXT"]},
            "bgcolor": COLORS["PANEL"],
            "steps": [
                {"range": [0, 25],  "color": "#660000"},
                {"range": [25, 50], "color": "#331a00"},
                {"range": [50, 75], "color": "#1a3300"},
                {"range": [75, 100],"color": "#006633"},
            ],
        },
    ))
    return fig


def _build_heatmap(df):
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="Weekly Δ Net MM — 2 Years", height=320))
    if df.empty:
        return fig
    s = df["mm_net"].diff().tail(104)
    weeks = s.index.isocalendar().week.values
    years = s.index.year.values
    unique_years = sorted(set(years))
    z = np.full((len(unique_years), 53), np.nan)
    for w, y, v in zip(weeks, years, s.values):
        z[unique_years.index(y), int(w) - 1] = v
    fig.add_trace(go.Heatmap(z=z, x=list(range(1, 54)),
                              y=[str(y) for y in unique_years],
                              colorscale="RdYlGn", zmid=0,
                              colorbar=dict(title="Δ Net",
                                            tickfont=dict(color=COLORS["TEXT"]))))
    fig.update_xaxes(title_text="Week of Year")
    return fig


def _build_momentum(df):
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="4W Net Positioning Momentum",
                                      height=340, showlegend=False))
    if df.empty:
        return fig
    s = df["mm_net"].tail(26)
    mom = s.diff(4).dropna()
    colors_arr = [COLORS["BULL"] if v > 0 else COLORS["BEAR"] for v in mom.values]
    fig.add_trace(go.Scatter(x=mom.index, y=mom.values, mode="lines+markers",
                              line=dict(color=COLORS["TEXT"], width=2),
                              marker=dict(color=colors_arr, size=10)))
    fig.add_hline(y=0, line_color=COLORS["GRID"])
    return fig


def _build_three_category(df):
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="Net Positioning by Category (26 weeks)", height=320))
    if df.empty:
        return fig
    last = df.tail(26)
    fig.add_trace(go.Scatter(x=last.index, y=last["mm_net"], mode="lines",
                              name="Managed Money", line=dict(color=COLORS["BULL"], width=3)))
    fig.add_trace(go.Scatter(x=last.index, y=last["prod_net"], mode="lines",
                              name="Producer/Merchant", line=dict(color=COLORS["ORANGE"], width=2)))
    fig.add_trace(go.Scatter(x=last.index, y=last["swap_net"], mode="lines",
                              name="Swap Dealer", line=dict(color=COLORS["BLUE"], width=2)))
    fig.add_hline(y=0, line_color=COLORS["GRID"])
    return fig


def _build_historical_signal(df):
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="Historical Signal: 2W Forward Return vs Positioning Percentile",
                                      height=320, showlegend=False))
    if df.empty or len(df) < 30:
        return fig
    px = futures.fetch_history("NG=F", period="5y")
    if px is None or px.empty:
        return fig
    px = px.copy()
    px.index = pd.to_datetime(px.index)
    if px.index.tz is not None:
        px.index = px.index.tz_localize(None)
    s = df["mm_net"]
    ranks = s.rank(pct=True) * 100
    pts_x = []; pts_y = []; pts_c = []
    for date, rank in ranks.items():
        if rank is None or pd.isna(rank) or not (rank > 90 or rank < 10):
            continue
        d_naive = date.tz_localize(None) if (hasattr(date, "tz") and date.tz is not None) else date
        future_window = px[px.index >= d_naive].head(11)
        if len(future_window) < 11:
            continue
        ret = (future_window["close"].iloc[-1] / future_window["close"].iloc[0]) - 1
        pts_x.append(rank)
        pts_y.append(ret * 100)
        pts_c.append(COLORS["BEAR"] if rank > 90 else COLORS["BULL"])
    if pts_x:
        fig.add_trace(go.Scatter(x=pts_x, y=pts_y, mode="markers",
                                  marker=dict(color=pts_c, size=10, line=dict(width=1, color="white"))))
    fig.add_hline(y=0, line_color=COLORS["GRID"])
    fig.update_xaxes(title_text="Positioning Percentile")
    fig.update_yaxes(title_text="2W Forward Return (%)")
    return fig


def figures_for_snapshot(stores):
    df = _df_from_store((stores or {}).get("cftc"))
    if df.empty:
        return []
    return [
        ("Positioning — MM Long/Short/Net", _build_mm_chart(df)),
        ("Positioning — Percentile Gauge",  _build_percentile_gauge(df)),
        ("Positioning — 2Y Heatmap",        _build_heatmap(df)),
        ("Positioning — 3-Category",        _build_three_category(df)),
        ("Positioning — Historical Signal", _build_historical_signal(df)),
    ]


def register_callbacks(app):

    @app.callback(
        Output("cftc-history-store", "data"),
        Input("cftc-interval", "n_intervals"),
        State("cftc-history-store", "data"),
    )
    def fetch_cftc(_, existing):
        now = dt.datetime.now(NY)
        should_fetch = True
        if existing and existing.get("last_fetch_iso"):
            try:
                last = dt.datetime.fromisoformat(existing["last_fetch_iso"])
                if last.tzinfo is None:
                    last = last.replace(tzinfo=NY)
                if (now - last).total_seconds() < 5 * 24 * 3600:
                    should_fetch = False
            except Exception:
                pass
        if existing and existing.get("records") and not should_fetch:
            return no_update
        df = cftc.build_3yr_history()
        if df.empty:
            return no_update
        df = df.reset_index()
        df["report_date"] = df["report_date"].astype(str)
        records = df.to_dict("records")
        return {"records": records, "last_fetch_iso": now.isoformat()}

    @app.callback(Output("cftc-mm-chart", "figure"),
                  Input("cftc-history-store", "data"))
    def mm_chart(d):  return _build_mm_chart(_df_from_store(d))

    @app.callback(Output("cftc-percentile-gauge", "figure"),
                  Input("cftc-history-store", "data"))
    def gauge(d):     return _build_percentile_gauge(_df_from_store(d))

    @app.callback(Output("cftc-heatmap", "figure"),
                  Input("cftc-history-store", "data"))
    def heatmap(d):   return _build_heatmap(_df_from_store(d))

    @app.callback(Output("cftc-momentum", "figure"),
                  Input("cftc-history-store", "data"))
    def momentum(d):  return _build_momentum(_df_from_store(d))

    @app.callback(Output("cftc-three-category", "figure"),
                  Input("cftc-history-store", "data"))
    def three_cat(d): return _build_three_category(_df_from_store(d))

    @app.callback(Output("cftc-historical-signal", "figure"),
                  Input("cftc-history-store", "data"))
    def hist_sig(d):  return _build_historical_signal(_df_from_store(d))
