"""StockTwits scraper — Twitter/X replacement for cashtag-driven sentiment.

The public endpoint `https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json`
returns recent messages for a symbol. No auth required for public streams;
unauthenticated rate-limit is ~200 req/hr per IP — comfortably above our needs
(20 tickers × every 15 min = 80 req/hr).
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Iterable

import requests

import config

logger = logging.getLogger(__name__)

_API_TEMPLATE = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_HEADERS = {"User-Agent": "NGSentimentBot/1.0", "Accept": "application/json"}
_TIMEOUT = 12

# NG_FUTURES is our virtual ticker; map it to StockTwits' actual cashtag for
# the front-month NG continuous futures contract.
_TICKER_ALIASES = {"NG_FUTURES": "NG_F"}


def fetch(tickers: Iterable[str] | None = None) -> list[dict]:
    syms = list(tickers or config.TICKERS_TO_TRACK)
    out: list[dict] = []
    for sym in syms:
        api_sym = _TICKER_ALIASES.get(sym, sym)
        try:
            url = _API_TEMPLATE.format(ticker=api_sym)
            r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        except Exception:
            logger.exception("StockTwits GET failed for %s", sym)
            continue
        if r.status_code == 404:
            # StockTwits returns 404 for unknown / unmapped symbols (NG_F may
            # not be quoted there). Quietly skip.
            continue
        if r.status_code == 429:
            logger.warning("StockTwits rate-limited on %s; skipping", sym)
            continue
        if r.status_code != 200:
            logger.warning("StockTwits %s for %s", r.status_code, sym)
            continue
        try:
            data = r.json()
        except Exception:
            logger.exception("StockTwits JSON parse failed for %s", sym)
            continue
        for msg in data.get("messages") or []:
            try:
                out.append(_normalise(msg, sym))
            except Exception:
                logger.exception("StockTwits normalise failed for %s", sym)
    logger.info("StockTwits: %d messages across %d symbols", len(out), len(syms))
    return out


def _normalise(msg: dict, watchlist_symbol: str) -> dict:
    body = (msg.get("body") or "").strip()
    user = (msg.get("user") or {})
    likes = ((msg.get("likes") or {}).get("total")) or 0
    created_at = msg.get("created_at")
    try:
        ts = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00")) \
            if created_at else dt.datetime.now(dt.timezone.utc)
    except Exception:
        ts = dt.datetime.now(dt.timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    url = f"https://stocktwits.com/{user.get('username') or 'anon'}/message/{msg.get('id')}"
    return {
        "source": "stocktwits",
        "source_name": f"${watchlist_symbol}",
        "text": body,
        "url": url,
        "author": user.get("username"),
        "engagement_score": float(likes),
        "created_at": ts,
        # StockTwits messages are explicitly tagged with the cashtag stream
        # we're pulling — they're on-topic by construction.
        "is_ng_native": True,
    }
