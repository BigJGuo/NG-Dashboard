"""Per-ticker, per-window sentiment aggregation.

Formulas follow the spec:

    raw_score        = positive - negative                       # -1..+1 per post
    engagement_wt    = log1p(engagement_score)
    recency_wt       = exp(-lambda * hours_since_post)           # lambda = 0.1
    sentiment_score  = mean(raw_i * engagement_wt_i * recency_wt_i) * 100
    bull_bear_ratio  = pos_count / (pos_count + neg_count)
    velocity         = current_1h_score - previous_1h_score
    composite_signal = 0.5*sentiment_score
                     + 0.3*((bull_bear_ratio - 0.5) * 200)
                     + 0.2*(volume_percentile * 100 - 50) * 2     # see notes

Composite is bounded to [-100, +100]. `volume_percentile` is the rank of the
current mention_volume against the last 30 days of windows for this ticker.

A new aggregated_signals row is inserted on every call. History is preserved
so velocity is derivable on the next call.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import statistics

from sqlalchemy import and_, select

import config
from . import sentiment_db

logger = logging.getLogger(__name__)

_WINDOW_HOURS = {"1h": 1, "4h": 4, "24h": 24}


def update_window(window: str, ticker: str | None = None) -> int:
    """Recompute the aggregate for `window` for every tracked ticker (or just
    the one passed). Returns rows written."""
    if window not in _WINDOW_HOURS:
        raise ValueError(f"Unknown window: {window}")
    tickers = [ticker] if ticker else list(config.TICKERS_TO_TRACK)
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(hours=_WINDOW_HOURS[window])

    written = 0
    for t in tickers:
        try:
            row = _compute(t, window, since, now)
        except Exception:
            logger.exception("aggregate failed for %s/%s", t, window)
            continue
        if row is None:
            continue
        sentiment_db.upsert_aggregate(row)
        written += 1
    logger.info("aggregator: wrote %d rows for window=%s", written, window)
    return written


# ── Internals ────────────────────────────────────────────────────────────────

def _compute(ticker: str, window: str,
             since: dt.datetime, now: dt.datetime) -> dict | None:
    posts = _fetch_window_posts(ticker, since)
    if not posts:
        # Still emit a zero-volume row so the dashboard sees the ticker as
        # tracked (volume=0, score=0, composite=0).
        return {
            "ticker": ticker, "window": window,
            "sentiment_score": 0.0, "mention_volume": 0,
            "bull_bear_ratio": None, "sentiment_velocity": _velocity(ticker, window, now, 0.0),
            "composite_signal": 0.0,
        }

    weighted_scores: list[float] = []
    pos_count = neg_count = 0
    for p in posts:
        raw = p["positive"] - p["negative"]            # -1..+1
        eng_wt = math.log1p(max(p["engagement_score"], 0.0))
        # tz-safe hours_since
        hours = max((now - p["created_at"]).total_seconds() / 3600.0, 0.0)
        rec_wt = math.exp(-config.SENTIMENT_DECAY_LAMBDA * hours)
        weighted_scores.append(raw * (1.0 + eng_wt) * rec_wt)
        if p["label"] == "positive":
            pos_count += 1
        elif p["label"] == "negative":
            neg_count += 1

    sentiment_score = (sum(weighted_scores) / len(weighted_scores)) * 100.0
    sentiment_score = max(-100.0, min(100.0, sentiment_score))

    bb_total = pos_count + neg_count
    bull_bear_ratio = (pos_count / bb_total) if bb_total else None

    mention_volume = len(posts)
    velocity = _velocity(ticker, window, now, sentiment_score)
    vol_percentile = _volume_percentile(ticker, window, mention_volume)

    # Composite — components weighted per spec, each rescaled to a [-100,+100]
    # contribution:
    #   sentiment_score  already on [-100, +100]
    #   bull_bear        (ratio - 0.5) * 200   → [-100, +100]
    #   volume_pct       (pct - 0.5) * 200     → [-100, +100]
    bb_component = ((bull_bear_ratio or 0.5) - 0.5) * 200.0
    vol_component = (vol_percentile - 0.5) * 200.0
    composite = (0.5 * sentiment_score) + (0.3 * bb_component) + (0.2 * vol_component)
    composite = max(-100.0, min(100.0, composite))

    return {
        "ticker": ticker, "window": window,
        "sentiment_score": sentiment_score,
        "mention_volume": mention_volume,
        "bull_bear_ratio": bull_bear_ratio,
        "sentiment_velocity": velocity,
        "composite_signal": composite,
    }


def _fetch_window_posts(ticker: str, since: dt.datetime) -> list[dict]:
    """Return per-post scored rows in the window for this ticker, joined with
    engagement and created_at from raw_posts."""
    with sentiment_db.session_scope() as s:
        rows = s.execute(
            select(
                sentiment_db.SentimentScore.positive,
                sentiment_db.SentimentScore.negative,
                sentiment_db.SentimentScore.label,
                sentiment_db.SentimentScore.confidence,
                sentiment_db.RawPost.engagement_score,
                sentiment_db.RawPost.created_at,
            )
            .join(sentiment_db.RawPost,
                  sentiment_db.RawPost.id == sentiment_db.SentimentScore.post_id)
            .where(and_(
                sentiment_db.SentimentScore.ticker == ticker,
                sentiment_db.RawPost.created_at >= since,
            ))
        ).all()
    out = []
    for r in rows:
        out.append({
            "positive": float(r[0]),
            "negative": float(r[1]),
            "label":    r[2],
            "confidence": float(r[3]),
            "engagement_score": float(r[4]),
            "created_at": r[5] if r[5].tzinfo else r[5].replace(tzinfo=dt.timezone.utc),
        })
    return out


def _velocity(ticker: str, window: str, now: dt.datetime,
              current_score: float) -> float | None:
    prev = sentiment_db.get_previous_aggregate(ticker, window, now)
    if not prev:
        return None
    return current_score - float(prev["sentiment_score"])


def _volume_percentile(ticker: str, window: str, current: int) -> float:
    history = sentiment_db.get_volume_history(
        ticker, window, days=config.RAW_POST_RETENTION_DAYS)
    if not history:
        return 0.5     # no baseline — assume median
    sorted_hist = sorted(history)
    # Fraction of history strictly less than current.
    less = sum(1 for h in sorted_hist if h < current)
    return less / len(sorted_hist)


def prune_old_raw_posts() -> int:
    """Wrapper around sentiment_db.prune_old_posts(); logs the count."""
    n = sentiment_db.prune_old_posts(config.RAW_POST_RETENTION_DAYS)
    logger.info("pruned %d raw_posts older than %d days",
                n, config.RAW_POST_RETENTION_DAYS)
    return n
