"""News tab — NG-focused FinBERT sentiment dashboard.

Layout:
    ┌────────────┬────────────────────────────────────────────────┐
    │  Filters   │  Ticker table  (sortable, click to drill in)   │
    │ (sidebar)  ├────────────────────────────────────────────────┤
    │            │  Detail panel for selected ticker:             │
    │            │    sentiment line  |  volume bars              │
    │            │    source pie      |  top bull / top bear      │
    └────────────┴────────────────────────────────────────────────┘

All data is read from the SQLAlchemy DB written by scheduler.py. The tab does
no scraping or scoring itself.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections import Counter

import plotly.graph_objects as go
from dash import dcc, html, Input, Output, State, dash_table, no_update
import dash_bootstrap_components as dbc

import config
from data import sentiment_db
from utils.theme import COLORS, plotly_layout

logger = logging.getLogger(__name__)

_WINDOW_HOURS = {"1h": 1, "4h": 4, "24h": 24}

_TABLE_COLUMNS = [
    {"name": "Ticker",     "id": "ticker"},
    {"name": "Composite",  "id": "composite_signal", "type": "numeric",
     "format": {"specifier": "+.1f"}},
    {"name": "Sentiment",  "id": "sentiment_score", "type": "numeric",
     "format": {"specifier": "+.1f"}},
    {"name": "Mentions",   "id": "mention_volume", "type": "numeric"},
    {"name": "Bull/Bear",  "id": "bull_bear_ratio_pct", "type": "numeric",
     "format": {"specifier": ".0%"}},
    {"name": "Velocity",   "id": "sentiment_velocity", "type": "numeric",
     "format": {"specifier": "+.1f"}},
]


# ── Layout ───────────────────────────────────────────────────────────────────

def layout():
    return html.Div([
        dcc.Interval(id="sentiment-refresh",
                     interval=config.SENTIMENT_DASHBOARD_REFRESH_MS),
        dcc.Store(id="sentiment-selected-ticker", storage_type="memory"),

        dbc.Row([
            dbc.Col(_sidebar(), width=2,
                    style={"borderRight": f"1px solid {COLORS['GRID']}",
                           "padding": "12px"}),
            dbc.Col([
                html.Div(id="sentiment-header",
                         style={"display": "flex", "justifyContent": "space-between",
                                "alignItems": "center", "marginBottom": "8px"}),
                dash_table.DataTable(
                    id="sentiment-table",
                    columns=_TABLE_COLUMNS,
                    sort_action="native",
                    row_selectable="single",
                    page_action="none",
                    cell_selectable=True,
                    style_table={"maxHeight": "280px", "overflowY": "auto"},
                    style_cell={"backgroundColor": COLORS["PANEL"],
                                "color": COLORS["TEXT"],
                                "border": f"1px solid {COLORS['GRID']}",
                                "fontSize": "12px", "padding": "6px 8px",
                                "textAlign": "right"},
                    style_cell_conditional=[
                        {"if": {"column_id": "ticker"}, "textAlign": "left",
                         "fontWeight": "700"},
                    ],
                    style_header={"backgroundColor": "#111",
                                  "color": COLORS["TEXT"],
                                  "fontWeight": "700",
                                  "border": f"1px solid {COLORS['GRID']}"},
                ),
                html.Div(id="sentiment-detail-panel",
                         style={"marginTop": "12px"}),
            ], width=10),
        ], className="g-0"),
    ])


def _sidebar():
    return html.Div([
        html.Div("Filters", style={"fontWeight": "700",
                                   "marginBottom": "12px",
                                   "color": COLORS["TEXT"]}),
        html.Div("Window", style={"fontSize": "11px",
                                  "color": COLORS["MUTED"]}),
        dcc.RadioItems(id="sentiment-window",
                       options=[{"label": w, "value": w}
                                for w in ("1h", "4h", "24h")],
                       value="1h",
                       inputStyle={"marginRight": "4px",
                                   "marginLeft": "8px"},
                       labelStyle={"display": "inline-block",
                                   "color": COLORS["TEXT"], "fontSize": "12px"}),
        html.Br(),
        html.Div("Min mentions",
                 style={"fontSize": "11px", "color": COLORS["MUTED"],
                        "marginTop": "8px"}),
        dcc.Slider(id="sentiment-min-mentions", min=0, max=50, step=1,
                   value=0, marks={0: "0", 25: "25", 50: "50"}),
        html.Div("Sources",
                 style={"fontSize": "11px", "color": COLORS["MUTED"],
                        "marginTop": "8px"}),
        dcc.Checklist(id="sentiment-sources",
                      options=[{"label": s, "value": s} for s in
                               ("reddit", "news", "stocktwits", "bluesky")],
                      value=["reddit", "news", "stocktwits", "bluesky"],
                      labelStyle={"display": "block",
                                  "color": COLORS["TEXT"],
                                  "fontSize": "12px"}),
    ])


# ── Snapshot hook (called by utils/snapshot.py) ──────────────────────────────

def figures_for_snapshot(stores: dict):
    """Build a static set of figures for the PDF snapshot. Reads directly from
    the DB; ignores `stores` (the new pipeline persists everything itself)."""
    figures = []
    try:
        latest = sentiment_db.get_latest_signals("1h")
    except Exception:
        return figures
    if not latest:
        return figures
    figures.append(("Sentiment — Composite by ticker (1h)",
                    _build_table_figure(latest)))
    # Top mover detail
    movers = sorted(latest, key=lambda r: abs(r["composite_signal"] or 0),
                    reverse=True)[:3]
    for m in movers:
        history = sentiment_db.get_history(m["ticker"], "1h", hours=24)
        figures.append((f"Sentiment — {m['ticker']} (24h)",
                        _build_score_chart(history, m["ticker"])))
    return figures


def _build_table_figure(rows: list[dict]) -> go.Figure:
    """A bar chart of composite signal per ticker — used for PDF snapshots."""
    rows_sorted = sorted(rows, key=lambda r: r["composite_signal"] or 0)
    tickers = [r["ticker"] for r in rows_sorted]
    composites = [r["composite_signal"] or 0 for r in rows_sorted]
    colors = [_signal_color(c) for c in composites]
    fig = go.Figure(go.Bar(x=composites, y=tickers, orientation="h",
                           marker_color=colors))
    fig.update_layout(**plotly_layout(title=None, height=400, showlegend=False))
    fig.update_xaxes(range=[-100, 100], title="Composite signal")
    return fig


# ── Callbacks ────────────────────────────────────────────────────────────────

def register_callbacks(app):

    @app.callback(
        Output("sentiment-header", "children"),
        Output("sentiment-table", "data"),
        Output("sentiment-table", "style_data_conditional"),
        Input("sentiment-refresh", "n_intervals"),
        Input("sentiment-window", "value"),
        Input("sentiment-min-mentions", "value"),
    )
    def update_table(_n, window, min_mentions):
        try:
            rows = sentiment_db.get_latest_signals(window)
            last_updated = sentiment_db.get_last_updated()
        except Exception:
            logger.exception("update_table DB read failed")
            rows, last_updated = [], None

        # Order: tracked watchlist first (so missing tickers still show as 0),
        # then any extra tickers discovered in the wild.
        by_ticker = {r["ticker"]: r for r in rows}
        ordered = []
        for t in config.TICKERS_TO_TRACK:
            ordered.append(by_ticker.get(t) or _empty_row(t, window))
        for t, r in by_ticker.items():
            if t not in config.TICKERS_TO_TRACK:
                ordered.append(r)
        ordered = [r for r in ordered
                   if (r["mention_volume"] or 0) >= (min_mentions or 0)]

        table_data = [_to_table_row(r) for r in ordered]
        style = _row_style_for(ordered)
        header = _build_header(last_updated, window, len(ordered))
        return header, table_data, style

    @app.callback(
        Output("sentiment-selected-ticker", "data"),
        Input("sentiment-table", "selected_rows"),
        Input("sentiment-table", "active_cell"),
        State("sentiment-table", "data"),
    )
    def store_selection(selected_rows, active_cell, data):
        if not data:
            return no_update
        idx = None
        if selected_rows:
            idx = selected_rows[0]
        elif active_cell and "row" in active_cell:
            idx = active_cell["row"]
        if idx is None or idx >= len(data):
            return no_update
        return data[idx]["ticker"]

    @app.callback(
        Output("sentiment-detail-panel", "children"),
        Input("sentiment-selected-ticker", "data"),
        Input("sentiment-window", "value"),
        Input("sentiment-sources", "value"),
        Input("sentiment-refresh", "n_intervals"),
    )
    def update_detail(ticker, window, sources, _n):
        if not ticker:
            return html.Div("Select a ticker above to drill in.",
                            style={"color": COLORS["MUTED"],
                                   "fontStyle": "italic",
                                   "padding": "20px"})
        try:
            history = sentiment_db.get_history(ticker, window, hours=24)
            since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
                hours=_WINDOW_HOURS.get(window, 24))
            posts = sentiment_db.get_posts_for_ticker(ticker, since, limit=200)
        except Exception:
            logger.exception("detail load failed for %s", ticker)
            history, posts = [], []

        if sources:
            posts = [p for p in posts if p["source"] in set(sources)]

        return _build_detail_panel(ticker, history, posts)


# ── Detail panel builders ────────────────────────────────────────────────────

def _build_detail_panel(ticker, history, posts):
    return html.Div([
        html.Div(f"{ticker} — detail",
                 style={"fontWeight": "700", "fontSize": "14px",
                        "marginBottom": "6px", "color": COLORS["TEXT"]}),
        dbc.Row([
            dbc.Col(dcc.Graph(figure=_build_score_chart(history, ticker),
                              config={"displayModeBar": False}), width=8),
            dbc.Col(dcc.Graph(figure=_build_source_pie(posts),
                              config={"displayModeBar": False}), width=4),
        ]),
        dcc.Graph(figure=_build_volume_chart(history, ticker),
                  config={"displayModeBar": False},
                  style={"height": "200px"}),
        dbc.Row([
            dbc.Col(_build_post_list(posts, kind="bull"), width=6),
            dbc.Col(_build_post_list(posts, kind="bear"), width=6),
        ], style={"marginTop": "8px"}),
    ])


def _build_score_chart(history, ticker) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**plotly_layout(
        title=f"Sentiment score — {ticker} (24h history)",
        height=260, showlegend=False))
    fig.update_yaxes(range=[-100, 100])
    if not history:
        return fig
    xs = [h["computed_at"] for h in history]
    ys = [h["sentiment_score"] for h in history]
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers",
                             line={"color": COLORS["BLUE"], "width": 2},
                             marker={"size": 5}))
    # 1h rolling mean overlay
    if len(ys) >= 3:
        rolling = _rolling_mean(ys, window=3)
        fig.add_trace(go.Scatter(x=xs, y=rolling, mode="lines",
                                 line={"color": COLORS["WARN"], "width": 1,
                                       "dash": "dot"}, name="1h roll"))
    return fig


def _build_volume_chart(history, ticker) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title=f"Mention volume — {ticker}",
                                       height=200, showlegend=False))
    if not history:
        return fig
    xs = [h["computed_at"] for h in history]
    ys = [h["mention_volume"] for h in history]
    fig.add_trace(go.Bar(x=xs, y=ys, marker_color=COLORS["BLUE"]))
    return fig


def _build_source_pie(posts) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**plotly_layout(title="Sources", height=260,
                                       showlegend=True))
    if not posts:
        return fig
    counts = Counter(p["source"] for p in posts)
    labels = list(counts.keys())
    values = [counts[k] for k in labels]
    palette = {"reddit": COLORS["ORANGE"], "news": COLORS["BLUE"],
               "stocktwits": COLORS["BULL"], "bluesky": COLORS["WARN"]}
    fig.add_trace(go.Pie(labels=labels, values=values, hole=0.45,
                         marker_colors=[palette.get(l, COLORS["MUTED"])
                                        for l in labels]))
    return fig


def _build_post_list(posts, kind: str):
    """kind = 'bull' shows top-5 most positive; 'bear' shows top-5 most
    negative."""
    title = "Top bullish" if kind == "bull" else "Top bearish"
    color = COLORS["BULL"] if kind == "bull" else COLORS["BEAR"]
    if not posts:
        body = html.Div("(no posts in window)",
                        style={"color": COLORS["MUTED"],
                               "fontStyle": "italic", "fontSize": "12px"})
        return html.Div([html.Div(title, style={"fontWeight": "700",
                                                "color": color,
                                                "marginBottom": "4px"}), body])

    def _score(p):
        pos = p.get("positive") or 0.0
        neg = p.get("negative") or 0.0
        return (pos - neg) if kind == "bull" else (neg - pos)

    candidates = [p for p in posts if p.get("positive") is not None]
    ranked = sorted(candidates, key=_score, reverse=True)[:5]
    items = []
    for p in ranked:
        snippet = (p["text"] or "")[:140].replace("\n", " ")
        if p.get("url"):
            link = html.A(snippet, href=p["url"], target="_blank",
                          style={"color": COLORS["TEXT"],
                                 "textDecoration": "none"})
        else:
            link = html.Span(snippet)
        meta = f"  {p['source_name'] or p['source']}"
        items.append(html.Li([
            link,
            html.Span(meta, style={"color": COLORS["MUTED"], "fontSize": "10px"}),
        ], style={"fontSize": "12px", "marginBottom": "4px"}))
    return html.Div([
        html.Div(title, style={"fontWeight": "700", "color": color,
                               "marginBottom": "4px"}),
        html.Ul(items, style={"paddingLeft": "20px", "margin": 0}),
    ])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_header(last_updated, window, n_rows):
    ts_text = "—"
    if last_updated:
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=dt.timezone.utc)
        ts_text = last_updated.astimezone().strftime("%H:%M:%S")
    return [
        html.Div([
            html.Span("Last updated: ",
                      style={"color": COLORS["MUTED"], "fontSize": "11px"}),
            html.Span(ts_text,
                      style={"color": COLORS["TEXT"], "fontSize": "12px",
                             "fontWeight": "700"}),
            html.Span(f"  ·  window={window}  ·  {n_rows} tickers",
                      style={"color": COLORS["MUTED"], "fontSize": "11px",
                             "marginLeft": "8px"}),
        ]),
        html.Div("Auto-refresh every 60s",
                 style={"color": COLORS["MUTED"], "fontSize": "11px"}),
    ]


def _to_table_row(r) -> dict:
    bb = r.get("bull_bear_ratio")
    return {
        "ticker": r["ticker"],
        "composite_signal": _round_or_none(r["composite_signal"], 1),
        "sentiment_score":  _round_or_none(r["sentiment_score"], 1),
        "mention_volume":   int(r["mention_volume"] or 0),
        "bull_bear_ratio_pct": (None if bb is None else round(bb, 3)),
        "sentiment_velocity": _round_or_none(r.get("sentiment_velocity"), 1),
    }


def _row_style_for(rows: list[dict]) -> list[dict]:
    styles: list[dict] = []
    for r in rows:
        c = r.get("composite_signal") or 0
        if c >= 30:
            color = COLORS["BULL"]
        elif c <= -30:
            color = COLORS["BEAR"]
        else:
            continue
        styles.append({
            "if": {"filter_query": f'{{ticker}} = "{r["ticker"]}"',
                   "column_id": "composite_signal"},
            "color": color, "fontWeight": "700",
        })
    return styles


def _empty_row(ticker, window) -> dict:
    return {
        "ticker": ticker, "window": window,
        "composite_signal": 0.0, "sentiment_score": 0.0,
        "mention_volume": 0, "bull_bear_ratio": None,
        "sentiment_velocity": None, "computed_at": None,
    }


def _round_or_none(v, ndigits: int):
    if v is None:
        return None
    return round(float(v), ndigits)


def _signal_color(value: float) -> str:
    if value is None:
        return COLORS["MUTED"]
    if value > 10:
        return COLORS["BULL"]
    if value < -10:
        return COLORS["BEAR"]
    return COLORS["MUTED"]


def _rolling_mean(xs: list[float], window: int) -> list[float]:
    if window <= 1 or not xs:
        return list(xs)
    out = []
    for i in range(len(xs)):
        lo = max(0, i - window + 1)
        chunk = xs[lo: i + 1]
        out.append(sum(chunk) / len(chunk))
    return out
