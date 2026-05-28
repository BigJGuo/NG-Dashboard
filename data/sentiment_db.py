"""SQLAlchemy 2.0 models + session manager for the NG sentiment pipeline.

SQLite by default. Set DB_URL in .env to a Postgres DSN to switch backends —
the schema and queries are portable.

Tables
------
raw_posts          one row per scraped post (Reddit, RSS article, StockTwits
                   message, Bluesky post). Identified by URL where possible.
post_tickers       many-to-many: a post can mention multiple tickers.
sentiment_scores   FinBERT output per (post, ticker). One score per ticker so
                   per-ticker confidence is preserved.
aggregated_signals per (ticker, window) snapshot computed by the aggregator.
alerts             threshold alerts fired by data/alerts_sentiment.py.

All write methods run inside `with Session.begin():` so failures roll back.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

from dotenv import load_dotenv
from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, create_engine, select,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_DB_URL = "sqlite:///data/sentiment.db"
_DB_URL = os.environ.get("DB_URL") or _DEFAULT_DB_URL

# For SQLite, ensure the parent directory exists before SQLAlchemy connects.
if _DB_URL.startswith("sqlite:///"):
    _sqlite_path = _DB_URL.replace("sqlite:///", "", 1)
    Path(_sqlite_path).parent.mkdir(parents=True, exist_ok=True)

# SQLite needs check_same_thread=False for use across APScheduler workers.
_engine_kwargs = {"future": True}
if _DB_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(_DB_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def engine_url() -> str:
    return str(engine.url)


# ── ORM models ───────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


class RawPost(Base):
    __tablename__ = "raw_posts"

    id              = Column(String(32),  primary_key=True, default=_uuid)
    source          = Column(String(32),  nullable=False)       # reddit/news/stocktwits/bluesky
    source_name     = Column(String(128), nullable=True)        # e.g. "r/naturalgas", "RBN Energy"
    text            = Column(Text,        nullable=False)
    url             = Column(String(512), nullable=True, unique=True)
    author          = Column(String(128), nullable=True)
    engagement_score = Column(Float, default=0.0, nullable=False)
    created_at      = Column(DateTime,    nullable=False)
    scraped_at      = Column(DateTime,    nullable=False,
                             default=lambda: dt.datetime.now(dt.timezone.utc))

    tickers = relationship("PostTicker", back_populates="post", cascade="all, delete-orphan")
    scores  = relationship("SentimentScore", back_populates="post", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_raw_posts_source_created", "source", "created_at"),
        Index("ix_raw_posts_created", "created_at"),
    )


class PostTicker(Base):
    """Many-to-many: a single post can mention several tickers."""
    __tablename__ = "post_tickers"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String(32), ForeignKey("raw_posts.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    ticker  = Column(String(16), nullable=False, index=True)

    post = relationship("RawPost", back_populates="tickers")

    __table_args__ = (
        UniqueConstraint("post_id", "ticker", name="uq_post_ticker"),
        Index("ix_post_tickers_ticker", "ticker"),
    )


class SentimentScore(Base):
    __tablename__ = "sentiment_scores"

    id          = Column(String(32), primary_key=True, default=_uuid)
    post_id     = Column(String(32), ForeignKey("raw_posts.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    ticker      = Column(String(16),  nullable=False)
    positive    = Column(Float,       nullable=False)
    negative    = Column(Float,       nullable=False)
    neutral     = Column(Float,       nullable=False)
    label       = Column(String(16),  nullable=False)    # positive/negative/neutral
    confidence  = Column(Float,       nullable=False)
    scored_at   = Column(DateTime,    nullable=False,
                         default=lambda: dt.datetime.now(dt.timezone.utc))

    post = relationship("RawPost", back_populates="scores")

    __table_args__ = (
        UniqueConstraint("post_id", "ticker", name="uq_score_post_ticker"),
        Index("ix_scores_ticker", "ticker"),
        Index("ix_scores_ticker_scored", "ticker", "scored_at"),
    )


class AggregatedSignal(Base):
    __tablename__ = "aggregated_signals"

    id                = Column(String(32), primary_key=True, default=_uuid)
    ticker            = Column(String(16),  nullable=False)
    window            = Column(String(8),   nullable=False)    # 1h / 4h / 24h
    sentiment_score   = Column(Float,       nullable=False)    # –100..+100
    mention_volume    = Column(Integer,     nullable=False)
    bull_bear_ratio   = Column(Float,       nullable=True)
    sentiment_velocity = Column(Float,      nullable=True)
    composite_signal  = Column(Float,       nullable=False)
    computed_at       = Column(DateTime,    nullable=False,
                               default=lambda: dt.datetime.now(dt.timezone.utc))

    __table_args__ = (
        Index("ix_agg_ticker_window_computed", "ticker", "window", "computed_at"),
        Index("ix_agg_computed", "computed_at"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id          = Column(String(32), primary_key=True, default=_uuid)
    ticker      = Column(String(16),  nullable=False)
    trigger     = Column(String(32),  nullable=False)    # composite/velocity/volume
    message     = Column(Text,        nullable=False)
    value       = Column(Float,       nullable=True)
    fired_at    = Column(DateTime,    nullable=False,
                         default=lambda: dt.datetime.now(dt.timezone.utc))

    __table_args__ = (
        Index("ix_alerts_fired", "fired_at"),
        Index("ix_alerts_ticker_trigger", "ticker", "trigger"),
    )


# ── Lifecycle ────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables if they don't already exist. Idempotent.

    SQLAlchemy's ``checkfirst`` skips existing tables but still attempts to
    create their indices, which fails if a prior schema version already
    materialised them. We catch those ``already exists`` errors so startup
    survives schema drift across runs without dropping the DB.
    """
    try:
        Base.metadata.create_all(engine)
    except Exception as e:
        if "already exists" not in str(e).lower():
            raise
        for table in Base.metadata.tables.values():
            try:
                table.create(engine, checkfirst=True)
            except Exception as te:
                if "already exists" not in str(te).lower():
                    raise
                logger.debug("Skipped existing DB object: %s", te)
    logger.info("DB initialised: %s", engine_url())


@contextmanager
def session_scope():
    """Yield a Session inside a transaction. Commits on success, rolls back on
    failure, always closes."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── Write helpers ────────────────────────────────────────────────────────────

def insert_post(post: dict, tickers: Iterable[str]) -> Optional[str]:
    """Insert one normalised post + its ticker mentions. Returns the post id,
    or None if the URL already exists (deduplication).

    Expected `post` keys:
        source, source_name, text, url, author, engagement_score, created_at
    """
    with session_scope() as s:
        url = post.get("url")
        if url:
            existing = s.execute(
                select(RawPost.id).where(RawPost.url == url)
            ).scalar_one_or_none()
            if existing:
                return None
        row = RawPost(
            source=post["source"],
            source_name=post.get("source_name"),
            text=post["text"],
            url=url,
            author=post.get("author"),
            engagement_score=float(post.get("engagement_score", 0.0)),
            created_at=post["created_at"],
        )
        s.add(row)
        s.flush()
        for t in {t for t in tickers if t}:
            s.add(PostTicker(post_id=row.id, ticker=t))
        return row.id


def insert_scores(post_id: str, scores_by_ticker: dict[str, dict]) -> None:
    """Insert one SentimentScore row per (post, ticker). Caller has already
    filtered by confidence; we just persist."""
    with session_scope() as s:
        for ticker, score in scores_by_ticker.items():
            s.add(SentimentScore(
                post_id=post_id,
                ticker=ticker,
                positive=float(score["positive"]),
                negative=float(score["negative"]),
                neutral=float(score["neutral"]),
                label=score["label"],
                confidence=float(score["confidence"]),
            ))


def upsert_aggregate(row: dict) -> None:
    """Insert a new aggregated_signals row. Append-only — we keep history so
    sentiment_velocity can be computed from the previous row."""
    with session_scope() as s:
        s.add(AggregatedSignal(
            ticker=row["ticker"],
            window=row["window"],
            sentiment_score=float(row["sentiment_score"]),
            mention_volume=int(row["mention_volume"]),
            bull_bear_ratio=(None if row.get("bull_bear_ratio") is None
                             else float(row["bull_bear_ratio"])),
            sentiment_velocity=(None if row.get("sentiment_velocity") is None
                                else float(row["sentiment_velocity"])),
            composite_signal=float(row["composite_signal"]),
        ))


def insert_alert(ticker: str, trigger: str, message: str, value: float | None) -> None:
    with session_scope() as s:
        s.add(Alert(ticker=ticker, trigger=trigger, message=message, value=value))


def prune_old_posts(older_than_days: int) -> int:
    """Delete raw_posts older than N days. SentimentScore + PostTicker rows
    cascade via ON DELETE. Returns rows deleted."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=older_than_days)
    with session_scope() as s:
        rows = s.execute(select(RawPost).where(RawPost.created_at < cutoff)).scalars().all()
        n = len(rows)
        for r in rows:
            s.delete(r)
        return n


# ── Read helpers ─────────────────────────────────────────────────────────────

def get_latest_signals(window: str) -> list[dict]:
    """Most recent aggregated_signals row per ticker for the given window."""
    with session_scope() as s:
        subq = (
            select(AggregatedSignal.ticker,
                   AggregatedSignal.computed_at.label("max_at"))
            .where(AggregatedSignal.window == window)
            .order_by(AggregatedSignal.ticker, AggregatedSignal.computed_at.desc())
        )
        # SQLite doesn't have DISTINCT ON; do it in Python.
        seen: dict[str, AggregatedSignal] = {}
        rows = s.execute(
            select(AggregatedSignal).where(AggregatedSignal.window == window)
            .order_by(AggregatedSignal.computed_at.desc())
        ).scalars().all()
        for r in rows:
            if r.ticker not in seen:
                seen[r.ticker] = r
        return [_agg_to_dict(r) for r in seen.values()]


def get_history(ticker: str, window: str, hours: int) -> list[dict]:
    """Aggregated_signals history for one ticker/window in the last N hours."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    with session_scope() as s:
        rows = s.execute(
            select(AggregatedSignal)
            .where(AggregatedSignal.ticker == ticker)
            .where(AggregatedSignal.window == window)
            .where(AggregatedSignal.computed_at >= cutoff)
            .order_by(AggregatedSignal.computed_at)
        ).scalars().all()
        return [_agg_to_dict(r) for r in rows]


def get_posts_for_ticker(ticker: str, since: dt.datetime,
                         limit: int = 200) -> list[dict]:
    """Posts mentioning `ticker` since `since`, joined with their sentiment
    score for that ticker (if any). Newest first."""
    with session_scope() as s:
        rows = s.execute(
            select(RawPost, SentimentScore)
            .join(PostTicker, PostTicker.post_id == RawPost.id)
            .join(SentimentScore,
                  (SentimentScore.post_id == RawPost.id) &
                  (SentimentScore.ticker == ticker), isouter=True)
            .where(PostTicker.ticker == ticker)
            .where(RawPost.created_at >= since)
            .order_by(RawPost.created_at.desc())
            .limit(limit)
        ).all()
        out = []
        for post, score in rows:
            out.append({
                "id": post.id,
                "source": post.source,
                "source_name": post.source_name,
                "text": post.text,
                "url": post.url,
                "author": post.author,
                "engagement_score": post.engagement_score,
                "created_at": post.created_at,
                "positive": score.positive if score else None,
                "negative": score.negative if score else None,
                "label": score.label if score else None,
                "confidence": score.confidence if score else None,
            })
        return out


def get_last_updated() -> Optional[dt.datetime]:
    with session_scope() as s:
        return s.execute(
            select(AggregatedSignal.computed_at)
            .order_by(AggregatedSignal.computed_at.desc())
            .limit(1)
        ).scalar_one_or_none()


def get_recent_alert() -> Optional[dict]:
    with session_scope() as s:
        row = s.execute(
            select(Alert).order_by(Alert.fired_at.desc()).limit(1)
        ).scalar_one_or_none()
        if not row:
            return None
        return {
            "ticker": row.ticker, "trigger": row.trigger,
            "message": row.message, "value": row.value,
            "fired_at": row.fired_at,
        }


def get_previous_aggregate(ticker: str, window: str,
                           before: dt.datetime) -> Optional[dict]:
    with session_scope() as s:
        row = s.execute(
            select(AggregatedSignal)
            .where(AggregatedSignal.ticker == ticker)
            .where(AggregatedSignal.window == window)
            .where(AggregatedSignal.computed_at < before)
            .order_by(AggregatedSignal.computed_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return _agg_to_dict(row) if row else None


def get_volume_history(ticker: str, window: str, days: int) -> list[int]:
    """List of mention_volume values for `ticker` in the last `days` days,
    used by alerts for baseline mean computation."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    with session_scope() as s:
        rows = s.execute(
            select(AggregatedSignal.mention_volume)
            .where(AggregatedSignal.ticker == ticker)
            .where(AggregatedSignal.window == window)
            .where(AggregatedSignal.computed_at >= cutoff)
        ).scalars().all()
        return [int(v) for v in rows]


def count_posts_since(since: dt.datetime) -> int:
    with session_scope() as s:
        rows = s.execute(
            select(RawPost.id).where(RawPost.created_at >= since)
        ).all()
        return len(rows)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _agg_to_dict(row: AggregatedSignal) -> dict:
    return {
        "ticker": row.ticker,
        "window": row.window,
        "sentiment_score": row.sentiment_score,
        "mention_volume": row.mention_volume,
        "bull_bear_ratio": row.bull_bear_ratio,
        "sentiment_velocity": row.sentiment_velocity,
        "composite_signal": row.composite_signal,
        "computed_at": row.computed_at,
    }
