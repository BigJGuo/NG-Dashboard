"""Threshold-crossing alerts for the sentiment pipeline.

Triggers (per config thresholds):
    1. |composite_signal| > ALERT_COMPOSITE_THRESHOLD          ("composite")
    2. |sentiment_velocity| > ALERT_VELOCITY_THRESHOLD          ("velocity")
    3. mention_volume > ALERT_VOLUME_MULTIPLIER * 7d_mean       ("volume")

Each fired alert:
    - is persisted to the `alerts` table (read by app.py for the nav-alert pill)
    - is logged at WARNING in logs/sentiment.log
    - is posted to DISCORD_WEBHOOK_URL when set, with 3-retry exponential
      backoff (handled by scraper_manager.with_retry)

In-memory dedup prevents the same (ticker, trigger) firing more than once per
ALERT_DEDUP_MINUTES.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import statistics
import threading

import requests
from dotenv import load_dotenv

import config
from . import scraper_manager, sentiment_db

load_dotenv()
logger = logging.getLogger(__name__)

_DEDUP_LOCK = threading.Lock()
_DEDUP_LAST_FIRED: dict[tuple[str, str], dt.datetime] = {}

_DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()


def check_thresholds(window: str = "1h") -> int:
    """Scan the latest aggregated_signals row for each ticker in `window`,
    fire any threshold crossings, return the number of new alerts fired."""
    fired = 0
    latest = sentiment_db.get_latest_signals(window)
    for sig in latest:
        for trigger, value, msg in _evaluate(sig, window):
            if _should_emit(sig["ticker"], trigger):
                _emit(sig["ticker"], trigger, msg, value)
                fired += 1
    if fired:
        logger.info("alerts: fired %d new alerts", fired)
    return fired


def _evaluate(sig: dict, window: str):
    ticker = sig["ticker"]
    composite = sig["composite_signal"]
    velocity  = sig.get("sentiment_velocity") or 0.0
    volume    = sig.get("mention_volume") or 0

    if abs(composite) >= config.ALERT_COMPOSITE_THRESHOLD:
        direction = "BULLISH" if composite > 0 else "BEARISH"
        yield ("composite", composite,
               f"{ticker}: composite {composite:+.1f} ({direction}) "
               f"crossed ±{config.ALERT_COMPOSITE_THRESHOLD:.0f}")

    if window == "1h" and abs(velocity) >= config.ALERT_VELOCITY_THRESHOLD:
        direction = "RISING" if velocity > 0 else "FALLING"
        yield ("velocity", velocity,
               f"{ticker}: sentiment {direction} {velocity:+.1f} pts in 1h")

    if volume > 0:
        history = sentiment_db.get_volume_history(ticker, window, days=7)
        baseline = statistics.mean(history) if history else 0.0
        if baseline > 0 and volume > config.ALERT_VOLUME_MULTIPLIER * baseline:
            yield ("volume", float(volume),
                   f"{ticker}: mention volume {volume} > "
                   f"{config.ALERT_VOLUME_MULTIPLIER}× 7d mean ({baseline:.1f})")


def _should_emit(ticker: str, trigger: str) -> bool:
    """Dedup: skip if same (ticker, trigger) fired within ALERT_DEDUP_MINUTES."""
    key = (ticker, trigger)
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(minutes=config.ALERT_DEDUP_MINUTES)
    with _DEDUP_LOCK:
        last = _DEDUP_LAST_FIRED.get(key)
        if last and last > cutoff:
            return False
        _DEDUP_LAST_FIRED[key] = now
    return True


def _emit(ticker: str, trigger: str, message: str, value: float) -> None:
    logger.warning("ALERT[%s/%s] %s", ticker, trigger, message)
    try:
        sentiment_db.insert_alert(ticker, trigger, message, value)
    except Exception:
        logger.exception("alert DB insert failed for %s/%s", ticker, trigger)

    if _DISCORD_WEBHOOK:
        try:
            scraper_manager.with_retry(
                lambda: _post_discord(message),
                description=f"discord webhook for {ticker}/{trigger}",
            )
        except Exception:
            # Already logged inside with_retry; don't propagate — the DB row
            # is the canonical record.
            pass


def _post_discord(message: str) -> None:
    r = requests.post(_DISCORD_WEBHOOK,
                      json={"content": f"⚡ **NG Sentiment Alert**\n{message}"},
                      timeout=5)
    if r.status_code >= 400:
        raise RuntimeError(f"discord HTTP {r.status_code}: {r.text[:200]}")
