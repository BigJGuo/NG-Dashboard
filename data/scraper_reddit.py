"""Reddit scraper: PRAW when REDDIT_CLIENT_ID is set, public JSON fallback
otherwise. Both paths emit the same normalised post dicts.

Subreddits listed in config.NG_NATIVE_SUBREDDITS bypass the NG-relevance
filter (everything in them is on-topic). General-investing subs require the
preprocessor's keyword/ticker check.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Iterable

import requests
from dotenv import load_dotenv

import config

load_dotenv()
logger = logging.getLogger(__name__)

_PUBLIC_HEADERS = {
    "User-Agent": os.environ.get("REDDIT_USER_AGENT",
                                 "NGSentimentBot/1.0 (anon)"),
    "Accept": "application/json",
}

_PRAW = None
_PRAW_TRIED = False


def _get_praw():
    """Return a PRAW Reddit instance if creds are available, else None.
    Caches the result so we only attempt construction once."""
    global _PRAW, _PRAW_TRIED
    if _PRAW_TRIED:
        return _PRAW
    _PRAW_TRIED = True

    cid = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    sec = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    ua  = os.environ.get("REDDIT_USER_AGENT", "").strip()
    if not (cid and sec and ua):
        logger.warning("Reddit creds missing — falling back to public JSON")
        return None
    try:
        import praw
        _PRAW = praw.Reddit(client_id=cid, client_secret=sec, user_agent=ua,
                            check_for_async=False)
        # Force read-only mode (script app without username/password).
        _PRAW.read_only = True
        logger.info("Reddit PRAW authenticated (read-only)")
        return _PRAW
    except Exception:
        logger.exception("PRAW init failed; using public JSON fallback")
        _PRAW = None
        return None


# ── Public entrypoint ────────────────────────────────────────────────────────

def fetch(subreddits: Iterable[str] | None = None,
          limit_per_sub: int = 30) -> list[dict]:
    """Return normalised post dicts from each subreddit. Skips low-engagement
    posts (score < ENGAGEMENT_FILTER_REDDIT_SCORE OR comments <
    ENGAGEMENT_FILTER_REDDIT_COMMENTS). Engagement filter is applied per the
    spec to reduce noise."""
    subs = list(subreddits or config.SUBREDDITS)
    reddit = _get_praw()
    fetcher = _fetch_via_praw if reddit else _fetch_via_public_json
    out: list[dict] = []
    for sub in subs:
        try:
            posts = fetcher(sub, limit_per_sub)
        except Exception:
            logger.exception("Reddit fetch failed for r/%s", sub)
            continue
        out.extend(posts)
    logger.info("Reddit: %d posts across %d subs", len(out), len(subs))
    return out


# ── PRAW path ────────────────────────────────────────────────────────────────

def _fetch_via_praw(sub: str, limit: int) -> list[dict]:
    reddit = _get_praw()
    if reddit is None:
        return []
    rows: list[dict] = []
    sr = reddit.subreddit(sub)
    # hot + new combined, dedup by id
    seen: set[str] = set()
    for listing in (sr.hot(limit=limit), sr.new(limit=limit // 2)):
        for s in listing:
            if s.id in seen:
                continue
            seen.add(s.id)
            if not _passes_engagement(s.score, s.num_comments):
                continue
            created = dt.datetime.fromtimestamp(getattr(s, "created_utc", 0),
                                                tz=dt.timezone.utc)
            text = (s.title or "") + ("\n" + s.selftext if s.selftext else "")
            rows.append({
                "source": "reddit",
                "source_name": f"r/{sub}",
                "text": text,
                "url": f"https://www.reddit.com{s.permalink}",
                "author": str(s.author) if s.author else None,
                "engagement_score": float(s.score + s.num_comments),
                "created_at": created,
                "is_ng_native": sub in config.NG_NATIVE_SUBREDDITS,
                # extras (not persisted but useful for upstream callers)
                "score": s.score,
                "num_comments": s.num_comments,
                "upvote_ratio": getattr(s, "upvote_ratio", None),
            })
    return rows


# ── Public-JSON fallback ─────────────────────────────────────────────────────

def _fetch_via_public_json(sub: str, limit: int) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for listing in ("hot", "new"):
        n = limit if listing == "hot" else max(limit // 2, 1)
        url = f"https://www.reddit.com/r/{sub}/{listing}.json?limit={n}"
        try:
            r = requests.get(url, headers=_PUBLIC_HEADERS, timeout=15)
        except Exception:
            logger.exception("Reddit public JSON GET failed for r/%s/%s", sub, listing)
            continue
        if r.status_code == 429:
            logger.warning("Reddit public JSON rate-limited on r/%s; backing off", sub)
            continue
        if r.status_code != 200:
            logger.warning("Reddit public JSON %s for r/%s/%s",
                           r.status_code, sub, listing)
            continue
        try:
            payload = r.json()
        except Exception:
            logger.exception("Reddit public JSON parse failed")
            continue
        children = (payload.get("data") or {}).get("children") or []
        for c in children:
            d = c.get("data") or {}
            sid = d.get("id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            score = int(d.get("score") or 0)
            ncom  = int(d.get("num_comments") or 0)
            if not _passes_engagement(score, ncom):
                continue
            created = dt.datetime.fromtimestamp(
                float(d.get("created_utc") or 0), tz=dt.timezone.utc)
            text = (d.get("title") or "")
            if d.get("selftext"):
                text += "\n" + d["selftext"]
            rows.append({
                "source": "reddit",
                "source_name": f"r/{sub}",
                "text": text,
                "url": "https://www.reddit.com" + (d.get("permalink") or ""),
                "author": d.get("author"),
                "engagement_score": float(score + ncom),
                "created_at": created,
                "is_ng_native": sub in config.NG_NATIVE_SUBREDDITS,
                "score": score,
                "num_comments": ncom,
                "upvote_ratio": d.get("upvote_ratio"),
            })
    return rows


def _passes_engagement(score: int, num_comments: int) -> bool:
    return (score >= config.ENGAGEMENT_FILTER_REDDIT_SCORE
            and num_comments >= config.ENGAGEMENT_FILTER_REDDIT_COMMENTS)
