"""yfinance helpers for NYMEX natural gas futures curve."""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None


MONTH_CODES = ["F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z"]


def contract_code(year: int, month: int) -> str:
    """Return the yfinance NYMEX ticker for a given delivery year/month, e.g. NGM25.NYM."""
    yy = year % 100
    return f"NG{MONTH_CODES[month - 1]}{yy:02d}.NYM"


def next_n_contracts(today: dt.date, n: int = 24) -> list[tuple[str, str, dt.date]]:
    """Return [(label, ticker, approximate_expiry_date)] for the next n contract months."""
    out = []
    y, m = today.year, today.month
    if today.day >= 26:
        m += 1
        if m > 12:
            m = 1; y += 1
    for _ in range(n):
        label = dt.date(y, m, 1).strftime("%b%y")
        ticker = contract_code(y, m)
        expiry = _ng_expiry_date(y, m)
        out.append((label, ticker, expiry))
        m += 1
        if m > 12:
            m = 1; y += 1
    return out


def _ng_expiry_date(year: int, month: int) -> dt.date:
    """NYMEX NG expiry is the 3rd-to-last business day of the month before delivery month."""
    delivery = dt.date(year, month, 1)
    prior = delivery - dt.timedelta(days=1)
    last_day = prior.replace(day=28)
    while True:
        nxt = last_day + dt.timedelta(days=1)
        if nxt.month != last_day.month:
            break
        last_day = nxt
    business_days = []
    d = last_day
    while len(business_days) < 5:
        if d.weekday() < 5:
            business_days.append(d)
        d -= dt.timedelta(days=1)
    return business_days[2]


def fetch_curve(contracts: list[tuple[str, str, dt.date]]) -> pd.DataFrame:
    """Fetch latest close for each contract. Returns DataFrame with [label, ticker, price, expiry]."""
    if yf is None or not contracts:
        return pd.DataFrame()
    tickers = [c[1] for c in contracts]
    try:
        df = yf.download(tickers, period="5d", interval="1d",
                         progress=False, auto_adjust=False, threads=True)
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        if "Close" in df.columns.get_level_values(0):
            closes = df["Close"]
        else:
            closes = df.xs("Close", axis=1, level=-1)
    else:
        closes = df[["Close"]]
        closes.columns = [tickers[0]]

    rows = []
    for label, ticker, expiry in contracts:
        if ticker in closes.columns:
            series = closes[ticker].dropna()
            price = float(series.iloc[-1]) if not series.empty else float("nan")
        else:
            price = float("nan")
        rows.append({"label": label, "ticker": ticker, "price": price, "expiry": expiry})
    return pd.DataFrame(rows)


def fetch_front_month_price() -> tuple[float, float, float]:
    """Return (price, change, pct_change) for the continuous front-month NG=F.

    Returns (nan, nan, nan) if yfinance is unavailable or fetch fails.
    """
    if yf is None:
        return float("nan"), float("nan"), float("nan")
    try:
        t = yf.Ticker("NG=F")
        hist = t.history(period="5d", interval="1d")
        if hist.empty:
            return float("nan"), float("nan"), float("nan")
        last = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else last
        change = last - prev
        pct = (change / prev * 100) if prev else 0.0
        return last, change, pct
    except Exception:
        return float("nan"), float("nan"), float("nan")


def fetch_history(ticker: str, period: str = "1y") -> pd.DataFrame:
    if yf is None:
        return pd.DataFrame()
    try:
        h = yf.Ticker(ticker).history(period=period, interval="1d")
        return h[["Close"]].rename(columns={"Close": "close"})
    except Exception:
        return pd.DataFrame()


def roll_yield(prices: list[float], expiries: list[dt.date]) -> list[float]:
    """Annualized roll yield between consecutive contracts."""
    out = []
    today = dt.date.today()
    for i in range(len(prices) - 1):
        near, far = prices[i], prices[i + 1]
        if not (near and far and not math.isnan(near) and not math.isnan(far)) or far == 0:
            out.append(float("nan"))
            continue
        days = max((expiries[i] - today).days, 1)
        ry = ((near / far) - 1) * (365 / days) * 100
        out.append(ry)
    return out


def regime_label(prices: list[float]) -> tuple[str, float]:
    """Classify the front-6 slope as CONTANGO/BACKWARDATION/MIXED."""
    valid = [p for p in prices[:6] if p and not math.isnan(p)]
    if len(valid) < 3:
        return "UNKNOWN", 0.0
    n = len(valid)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(valid) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, valid))
    den = sum((x - mean_x) ** 2 for x in xs) or 1
    slope = num / den
    if slope >  0.02: return "CONTANGO", slope
    if slope < -0.02: return "BACKWARDATION", slope
    return "MIXED", slope
