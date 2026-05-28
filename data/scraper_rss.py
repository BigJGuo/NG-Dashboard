"""RSS news scraper. Emits normalised post dicts identical in shape to the
other scrapers.

Feeds in config.NG_NATIVE_FEEDS bypass the NG-relevance filter (RBN, EIA,
Rigzone, Hart Energy — already on-topic). Other feeds (Reuters, Benzinga,
Seeking Alpha) pass through the preprocessor's keyword/ticker filter.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Iterable

import feedparser

import config

logger = logging.getLogger(__name__)


def fetch(feeds: Iterable[tuple[str, str]] | None = None) -> list[dict]:
    """Parse each (source_name, url) pair. Tolerant of malformed feeds —
    individual failures log and are skipped."""
    feeds = list(feeds or config.RSS_FEEDS)
    out: list[dict] = []
    for name, url in feeds:
        try:
            parsed = feedparser.parse(url, request_headers={
                "User-Agent": "NGSentimentBot/1.0 (RSS reader)",
            })
        except Exception:
            logger.exception("feedparser raised for %s", name)
            continue
        if getattr(parsed, "bozo", 0) and parsed.entries == []:
            logger.warning("feed %s bozo, 0 entries: %r",
                           name, getattr(parsed, "bozo_exception", None))
            continue
        for entry in parsed.entries:
            try:
                out.append(_normalise(entry, name))
            except Exception:
                logger.exception("RSS entry normalise failed for %s", name)
    logger.info("RSS: %d entries across %d feeds", len(out), len(feeds))
    return out


def _normalise(entry, source_name: str) -> dict:
    title = (getattr(entry, "title", "") or "").strip()
    summary = (getattr(entry, "summary", "") or "").strip()
    text = title if not summary else f"{title}\n{summary}"
    url = (getattr(entry, "link", "") or "").strip() or None

    # Try the various date attributes feedparser populates.
    ts = None
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        st = getattr(entry, attr, None)
        if st:
            try:
                ts = dt.datetime.fromtimestamp(time.mktime(st), tz=dt.timezone.utc)
                break
            except Exception:
                continue
    if ts is None:
        ts = dt.datetime.now(dt.timezone.utc)

    return {
        "source": "news",
        "source_name": source_name,
        "text": text,
        "url": url,
        "author": getattr(entry, "author", None),
        "engagement_score": 0.0,        # RSS has no engagement signal
        "created_at": ts,
        "is_ng_native": source_name in config.NG_NATIVE_FEEDS,
    }
