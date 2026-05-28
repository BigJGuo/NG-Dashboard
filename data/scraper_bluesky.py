"""Bluesky scraper via the public AT Protocol `searchPosts` endpoint.

No authentication required for read-only search. Queries are NG-themed:
cashtags for each watchlist ticker plus a few macro phrases. Results pass
through the preprocessor's relevance filter — Bluesky search is broad and
occasional false positives slip in.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Iterable

import requests

import config

logger = logging.getLogger(__name__)

_API_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
_HEADERS = {"User-Agent": "NGSentimentBot/1.0", "Accept": "application/json"}
_TIMEOUT = 12

# Macro/topical queries on top of per-ticker cashtag queries.
_BASE_QUERIES = ["natural gas", "henry hub", "LNG export", "$NG", "EIA storage"]


def _default_queries() -> list[str]:
    cashtags = [f"${t}" for t in config.TICKERS_TO_TRACK
                if t not in {"NG_FUTURES"}]
    return cashtags + _BASE_QUERIES


def fetch(queries: Iterable[str] | None = None,
          limit_per_query: int = 25) -> list[dict]:
    qs = list(queries or _default_queries())
    out: list[dict] = []
    for q in qs:
        try:
            r = requests.get(_API_URL,
                             params={"q": q, "limit": min(limit_per_query, 100)},
                             headers=_HEADERS, timeout=_TIMEOUT)
        except Exception:
            logger.exception("Bluesky GET failed for %r", q)
            continue
        if r.status_code == 429:
            logger.warning("Bluesky rate-limited on %r; skipping", q)
            continue
        if r.status_code != 200:
            logger.warning("Bluesky %s for %r", r.status_code, q)
            continue
        try:
            data = r.json()
        except Exception:
            logger.exception("Bluesky JSON parse failed for %r", q)
            continue
        for post in data.get("posts") or []:
            try:
                out.append(_normalise(post, q))
            except Exception:
                logger.exception("Bluesky normalise failed for %r", q)
    # Dedup by URI before returning (same post may match multiple queries)
    deduped = {p["url"]: p for p in out if p.get("url")}.values()
    result = list(deduped)
    logger.info("Bluesky: %d posts across %d queries (%d after dedup)",
                len(out), len(qs), len(result))
    return result


def _normalise(post: dict, query: str) -> dict:
    record = post.get("record") or {}
    author = post.get("author") or {}
    text = (record.get("text") or "").strip()
    handle = author.get("handle") or "unknown"
    uri = post.get("uri") or ""
    # Convert at:// URI to a human web link.
    rkey = uri.rsplit("/", 1)[-1] if uri else ""
    web_url = f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else None

    created_at = record.get("createdAt") or post.get("indexedAt")
    try:
        ts = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00")) \
            if created_at else dt.datetime.now(dt.timezone.utc)
    except Exception:
        ts = dt.datetime.now(dt.timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)

    likes = int(post.get("likeCount") or 0)
    reposts = int(post.get("repostCount") or 0)
    replies = int(post.get("replyCount") or 0)

    return {
        "source": "bluesky",
        "source_name": f"bsky:{query}",
        "text": text,
        "url": web_url,
        "author": handle,
        "engagement_score": float(likes + reposts + replies),
        "created_at": ts,
        # Bluesky search is too broad to bypass relevance filtering.
        "is_ng_native": False,
    }
