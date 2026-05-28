"""NG Trading Intelligence Dashboard — single-file Plotly Dash app.

Run with: python app.py
Then open http://127.0.0.1:8055
"""
from __future__ import annotations

import datetime as dt
import math

import dash
from dash import dcc, html, Input, Output, State, callback_context, no_update
import dash_bootstrap_components as dbc
import pytz

import config
from utils.theme import COLORS
from data import futures as fdata
from data import sentiment_db, finbert_scorer

from tabs import storage as tab_storage
from tabs import weather as tab_weather
from tabs import news as tab_news
from tabs import positioning as tab_positioning
from tabs import curve as tab_curve
from tabs import regional_storage as tab_regional_storage
from utils.snapshot import build_pdf_snapshot
import scheduler as sentiment_scheduler

NY = pytz.timezone("America/New_York")

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.CYBORG],
    suppress_callback_exceptions=True,
    title="NG Trading Intelligence",
)
server = app.server


def build_navbar():
    return dbc.Navbar(
        dbc.Container([
            html.Div("NG TRADING INTELLIGENCE", className="navbar-brand"),
            html.Div([
                html.Div([
                    html.Div("Front Month", className="nav-cell-title"),
                    html.Div(id="nav-price", className="nav-price-big", children="—"),
                    html.Div(id="nav-price-change", style={"fontSize": "12px"}, children=""),
                ], className="nav-cell"),
                html.Div([
                    html.Div("Storage vs 5Y Avg", className="nav-cell-title"),
                    html.Div(id="nav-storage", className="nav-cell-value", children="—"),
                ], className="nav-cell"),
                html.Div([
                    html.Div("New York", className="nav-cell-title"),
                    html.Div(id="nav-clock", className="nav-cell-value", children="—"),
                ], className="nav-cell"),
                html.Div([
                    html.Div("Sentiment", className="nav-cell-title"),
                    html.Span(id="nav-sentiment-dot", className="sentiment-dot",
                              style={"color": COLORS["MUTED"], "backgroundColor": COLORS["MUTED"]}),
                ], className="nav-cell"),
            ], style={"display": "flex", "alignItems": "center", "flex": "1",
                      "justifyContent": "center"}),
            html.Div([
                html.Div(id="nav-alert", className="nav-cell-value",
                         style={"maxWidth": "320px", "fontSize": "12px",
                                "whiteSpace": "nowrap", "overflow": "hidden",
                                "textOverflow": "ellipsis", "padding": "0 12px"}),
                dbc.Button("Settings", id="settings-open", color="secondary",
                           size="sm", className="me-2"),
                dbc.Button("Snapshot", id="snapshot-btn", color="primary", size="sm"),
            ], style={"display": "flex", "alignItems": "center"}),
        ], fluid=True, style={"display": "flex", "alignItems": "center",
                              "justifyContent": "space-between"}),
        className="ng-navbar", fixed="top", dark=True,
    )


def build_settings_modal():
    city_options = [{"label": c["name"], "value": c["name"]} for c in config.CITIES]
    default_city_values = [c["name"] for c in config.CITIES]
    return dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Settings")),
        dbc.ModalBody([
            dbc.Label("EIA API Key"),
            dbc.Input(id="settings-eia-key", type="text", placeholder="paste EIA v2 API key",
                      value=config.EIA_API_KEY_FALLBACK),
            html.Br(),
            dbc.Label("Weather refresh interval (minutes)"),
            dbc.Input(id="settings-weather-mins", type="number",
                      value=config.DEFAULT_INTERVALS["weather_ms"] // 60000, min=1),
            html.Br(),
            dbc.Label("Cities to include"),
            dbc.Checklist(id="settings-cities", options=city_options,
                          value=default_city_values, inline=True,
                          style={"maxHeight": "180px", "overflowY": "auto"}),
            html.Br(),
            dbc.Label("Sentiment tickers (one per line — restart to apply)"),
            dbc.Textarea(id="settings-sentiment-tickers",
                         value="\n".join(config.TICKERS_TO_TRACK),
                         style={"height": "100px"}),
        ]),
        dbc.ModalFooter([
            dbc.Button("Save", id="settings-save", color="success"),
            dbc.Button("Close", id="settings-close", color="secondary"),
        ]),
    ], id="settings-modal", is_open=False, size="lg")


def build_stores():
    return html.Div([
        dcc.Store(id="settings-store", storage_type="local"),
        dcc.Store(id="storage-history-store", storage_type="memory"),
        dcc.Store(id="weather-data-store", storage_type="memory"),
        dcc.Store(id="weather-yesterday-store", storage_type="local"),
        dcc.Store(id="weather-3d-ago-store", storage_type="local"),
        dcc.Store(id="cftc-history-store", storage_type="local"),
        dcc.Store(id="curve-history-store", storage_type="local"),
        dcc.Store(id="front-month-store", storage_type="memory"),
        dcc.Store(id="trajectory-store", storage_type="memory"),
        dcc.Store(id="trajectory-last-calculated", storage_type="memory"),
        dcc.Store(id="regional-storage-data", storage_type="memory"),
        dcc.Interval(id="clock-interval", interval=config.DEFAULT_INTERVALS["clock_ms"]),
        dcc.Interval(id="navbar-price-interval",
                     interval=config.DEFAULT_INTERVALS["navbar_price_ms"]),
        # Nav sentiment dot + nav alert refresh from the sentiment DB on this
        # interval. Decoupled from the in-tab refresh so the navbar stays
        # responsive even when the News tab is closed.
        dcc.Interval(id="nav-sentiment-interval",
                     interval=config.SENTIMENT_DASHBOARD_REFRESH_MS),
        dcc.Download(id="download"),
    ])


app.layout = html.Div([
    build_navbar(),
    build_settings_modal(),
    build_stores(),
    html.Div([
        dbc.Tabs(id="tabs", active_tab="storage", children=[
            dbc.Tab(label="Storage",     tab_id="storage"),
            dbc.Tab(label="Weather",     tab_id="weather"),
            dbc.Tab(label="News",        tab_id="news"),
            dbc.Tab(label="Positioning", tab_id="positioning"),
            dbc.Tab(label="Curve",       tab_id="curve"),
        ]),
        html.Div(id="tab-content"),
    ], className="dashboard-container"),
])


@app.callback(Output("tab-content", "children"), Input("tabs", "active_tab"))
def render_tab(active):
    if   active == "storage":     return tab_storage.layout()
    elif active == "weather":     return tab_weather.layout()
    elif active == "news":        return tab_news.layout()
    elif active == "positioning": return tab_positioning.layout()
    elif active == "curve":       return tab_curve.layout()
    return html.Div("Unknown tab")


# ── Navbar callbacks ────────────────────────────────────────────────────────

@app.callback(Output("nav-clock", "children"), Input("clock-interval", "n_intervals"))
def update_clock(_):
    return dt.datetime.now(NY).strftime("%H:%M:%S ET")


@app.callback(
    Output("nav-price", "children"),
    Output("nav-price-change", "children"),
    Output("nav-price-change", "style"),
    Output("front-month-store", "data"),
    Input("navbar-price-interval", "n_intervals"),
)
def update_navbar_price(_):
    price, change, pct = fdata.fetch_front_month_price()
    if price is None or (isinstance(price, float) and math.isnan(price)):
        return "—", "", {"fontSize": "12px", "color": COLORS["MUTED"]}, no_update
    color = COLORS["BULL"] if change >= 0 else COLORS["BEAR"]
    arrow = "▲" if change >= 0 else "▼"
    change_text = f"{arrow} {change:+.3f} ({pct:+.2f}%)"
    return (f"${price:.3f}", change_text,
            {"fontSize": "12px", "color": color, "fontWeight": "700"},
            {"price": price, "change": change, "pct": pct})


@app.callback(
    Output("nav-storage", "children"),
    Output("nav-storage", "style"),
    Input("storage-history-store", "data"),
)
def update_navbar_storage(data):
    if not data or "deviation" not in data:
        return "—", {"color": COLORS["MUTED"]}
    deviation = data["deviation"]
    color = COLORS["BULL"] if deviation > 0 else (COLORS["BEAR"] if deviation < 0 else COLORS["MUTED"])
    return f"{deviation:+,.0f} Bcf", {"color": color, "fontWeight": "700"}


@app.callback(
    Output("nav-sentiment-dot", "style"),
    Input("nav-sentiment-interval", "n_intervals"),
)
def update_sentiment_dot(_):
    """Show the NG_FUTURES composite signal as a colored dot.

    Positive composite → green, negative → red, low-magnitude → amber, no
    data → muted grey.
    """
    try:
        rows = sentiment_db.get_latest_signals("1h")
    except Exception:
        return {"color": COLORS["MUTED"], "backgroundColor": COLORS["MUTED"]}
    target = next((r for r in rows if r["ticker"] == config.NG_MACRO_BUCKET), None)
    if not target:
        # Fall back to mean composite across watchlist if NG_FUTURES not populated.
        tracked = [r for r in rows if r["ticker"] in set(config.TICKERS_TO_TRACK)]
        if not tracked:
            return {"color": COLORS["MUTED"], "backgroundColor": COLORS["MUTED"]}
        score = sum(r["composite_signal"] or 0 for r in tracked) / len(tracked)
    else:
        score = target["composite_signal"] or 0
    if score >  20: color = COLORS["BULL"]
    elif score < -20: color = COLORS["BEAR"]
    elif abs(score) > 0.5: color = COLORS["WARN"]
    else: color = COLORS["MUTED"]
    return {"color": color, "backgroundColor": color}


@app.callback(
    Output("nav-alert", "children"),
    Output("nav-alert", "className"),
    Input("nav-sentiment-interval", "n_intervals"),
)
def update_nav_alert(_):
    """Surface the most recent sentiment alert in the navbar pill."""
    try:
        alert = sentiment_db.get_recent_alert()
    except Exception:
        return "", ""
    if not alert:
        return "", ""
    # Only show alerts fired in the last 2 hours; older ones aren't fresh.
    fired = alert["fired_at"]
    if fired and fired.tzinfo is None:
        fired = fired.replace(tzinfo=dt.timezone.utc)
    if fired and (dt.datetime.now(dt.timezone.utc) - fired) > dt.timedelta(hours=2):
        return "", ""
    text = (alert["message"] or "")[:120]
    value = alert.get("value") or 0
    css = "flash-green" if value > 0 else "flash-red"
    return text, css


# ── Settings modal ──────────────────────────────────────────────────────────

@app.callback(
    Output("settings-modal", "is_open"),
    Input("settings-open", "n_clicks"),
    Input("settings-close", "n_clicks"),
    Input("settings-save", "n_clicks"),
    State("settings-modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_settings(open_click, close_click, save_click, is_open):
    return not is_open


@app.callback(
    Output("settings-store", "data"),
    Input("settings-save", "n_clicks"),
    State("settings-eia-key", "value"),
    State("settings-weather-mins", "value"),
    State("settings-cities", "value"),
    State("settings-sentiment-tickers", "value"),
    State("settings-store", "data"),
    prevent_initial_call=True,
)
def save_settings(_, eia_key, weather_mins, cities, sentiment_tickers, existing):
    tickers_list = [t.strip().upper() for t in
                    (sentiment_tickers or "").replace(",", "\n").split("\n")
                    if t.strip()]
    return {
        "eia_key": (eia_key or "").strip(),
        "weather_mins": int(weather_mins or 10),
        "cities": cities or [c["name"] for c in config.CITIES],
        "sentiment_tickers": tickers_list or list(config.TICKERS_TO_TRACK),
    }


# ── Snapshot PDF download ───────────────────────────────────────────────────

@app.callback(
    Output("download", "data"),
    Input("snapshot-btn", "n_clicks"),
    State("storage-history-store", "data"),
    State("weather-data-store", "data"),
    State("cftc-history-store", "data"),
    State("curve-history-store", "data"),
    State("front-month-store", "data"),
    prevent_initial_call=True,
)
def snapshot(n_clicks, storage_data, weather_data,
             cftc_data, curve_data, front_data):
    if not n_clicks:
        return no_update
    # News-tab figures now read directly from the sentiment DB inside
    # tabs/news.py::figures_for_snapshot, so no news payload is passed.
    stores = {
        "storage": storage_data, "weather": weather_data, "news": None,
        "cftc": cftc_data, "curve": curve_data, "front": front_data,
    }
    pdf_bytes = build_pdf_snapshot(stores)
    filename = f"ng-snapshot-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
    return dcc.send_bytes(lambda buf: buf.write(pdf_bytes), filename)


# ── Register per-tab callbacks ──────────────────────────────────────────────

tab_storage.register_callbacks(app)
tab_weather.register_callbacks(app)
tab_news.register_callbacks(app)
tab_positioning.register_callbacks(app)
tab_curve.register_callbacks(app)
tab_regional_storage.register_callbacks(app)


# ── Sentiment pipeline startup ──────────────────────────────────────────────
# Initialise the sentiment DB, preload FinBERT, and start the background
# scheduler. Done at import time (rather than inside __main__) so it also
# fires under `gunicorn app:server` deployments.
sentiment_db.init_db()
finbert_scorer.preload()           # blocks while ~440MB downloads on first run
sentiment_scheduler.start_scheduler()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8055, use_reloader=False)
