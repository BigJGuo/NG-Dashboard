"""APScheduler background jobs for the NG sentiment pipeline.

Three jobs:
    every 15 min  → scrape + preprocess + score + insert + update 1h aggregates
                    + check alerts
    every 1 hour  → update 4h aggregates
    every 6 hours → update 24h aggregates + prune raw_posts older than 30 days

Logging goes to logs/sentiment.log via a rotating handler (10MB × 5).

`start_scheduler()` is idempotent — calling twice is a no-op. The scheduler
runs in daemon threads so it dies with the Dash process.
"""
from __future__ import annotations

import logging
import logging.handlers
import threading
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

import config
from data import (alerts_sentiment, finbert_scorer, preprocessor,
                  scraper_manager, sentiment_db, signal_aggregator)

logger = logging.getLogger(__name__)

_LOG_PATH = Path("logs/sentiment.log")
_LOG_INITIALISED = False

_SCHEDULER: BackgroundScheduler | None = None
_START_LOCK = threading.Lock()


def _init_logging() -> None:
    global _LOG_INITIALISED
    if _LOG_INITIALISED:
        return
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        _LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    # Don't duplicate Dash's stdout handler.
    if not any(getattr(h, "baseFilename", None) == str(_LOG_PATH.resolve())
               for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    _LOG_INITIALISED = True


# ── Job bodies ───────────────────────────────────────────────────────────────

def job_15min() -> None:
    """End-to-end scrape → score → aggregate(1h) → alert."""
    logger.info("[job_15min] start")
    try:
        raw = scraper_manager.run_all()
    except Exception:
        logger.exception("[job_15min] scraper_manager.run_all failed")
        return

    try:
        processed = preprocessor.process(raw)
    except Exception:
        logger.exception("[job_15min] preprocessor failed")
        return

    if not processed:
        logger.info("[job_15min] no relevant posts; aggregating empty windows")
        try:
            signal_aggregator.update_window("1h")
            alerts_sentiment.check_thresholds("1h")
        except Exception:
            logger.exception("[job_15min] aggregate/alerts on empty failed")
        return

    # Score
    try:
        score_rows = finbert_scorer.score_batch([p["cleaned_text"] for p in processed])
    except Exception:
        logger.exception("[job_15min] finbert scoring failed")
        return

    # Persist posts + scores
    written_posts = written_scores = 0
    for post, score in zip(processed, score_rows):
        if score["confidence"] < config.MIN_CONFIDENCE_THRESHOLD:
            continue
        try:
            post_id = sentiment_db.insert_post(post, post["tickers"])
        except Exception:
            logger.exception("[job_15min] insert_post failed")
            continue
        if not post_id:
            # Duplicate URL; skip score write.
            continue
        written_posts += 1
        scores_by_ticker = {t: score for t in post["tickers"]}
        try:
            sentiment_db.insert_scores(post_id, scores_by_ticker)
            written_scores += len(scores_by_ticker)
        except Exception:
            logger.exception("[job_15min] insert_scores failed")
    logger.info("[job_15min] wrote %d posts, %d scores", written_posts, written_scores)

    # Aggregate + alert
    try:
        signal_aggregator.update_window("1h")
    except Exception:
        logger.exception("[job_15min] aggregate(1h) failed")
    try:
        alerts_sentiment.check_thresholds("1h")
    except Exception:
        logger.exception("[job_15min] alerts check failed")
    logger.info("[job_15min] done")


def job_hourly() -> None:
    logger.info("[job_hourly] update 4h aggregates")
    try:
        signal_aggregator.update_window("4h")
    except Exception:
        logger.exception("[job_hourly] aggregate(4h) failed")


def job_six_hourly() -> None:
    logger.info("[job_six_hourly] update 24h aggregates + prune")
    try:
        signal_aggregator.update_window("24h")
    except Exception:
        logger.exception("[job_six_hourly] aggregate(24h) failed")
    try:
        signal_aggregator.prune_old_raw_posts()
    except Exception:
        logger.exception("[job_six_hourly] prune failed")


# ── Lifecycle ────────────────────────────────────────────────────────────────

def start_scheduler() -> BackgroundScheduler:
    """Idempotent. Returns the running scheduler."""
    global _SCHEDULER
    with _START_LOCK:
        if _SCHEDULER is not None and _SCHEDULER.running:
            return _SCHEDULER
        _init_logging()
        # Ensure the DB exists before the first job runs.
        try:
            sentiment_db.init_db()
        except Exception:
            logger.exception("init_db failed (continuing — jobs will retry)")

        sched = BackgroundScheduler(daemon=True, timezone="UTC")
        sched.add_job(job_15min, "interval",
                      minutes=config.SCRAPE_INTERVAL_MINUTES,
                      id="sentiment_15min", max_instances=1,
                      coalesce=True, replace_existing=True,
                      next_run_time=_in_seconds(20))
        sched.add_job(job_hourly, "interval", hours=1,
                      id="sentiment_hourly", max_instances=1,
                      coalesce=True, replace_existing=True)
        sched.add_job(job_six_hourly, "interval", hours=6,
                      id="sentiment_six_hourly", max_instances=1,
                      coalesce=True, replace_existing=True)
        sched.start()
        _SCHEDULER = sched
        logger.info("scheduler started (15min/1h/6h)")
        return sched


def _in_seconds(secs: int):
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=secs)


def shutdown_scheduler() -> None:
    global _SCHEDULER
    with _START_LOCK:
        if _SCHEDULER is not None:
            _SCHEDULER.shutdown(wait=False)
            _SCHEDULER = None
            logger.info("scheduler shutdown")
