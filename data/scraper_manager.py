"""Unified scraper orchestration + a shared retry helper.

`run_all()` calls every source in turn, catches per-source errors, and returns
one flat list of normalised post dicts. Individual source failures do not
kill the run — other sources continue.

`with_retry()` is the exponential-backoff helper used by every external call
in the codebase (Reddit JSON fallback, RSS, StockTwits, Bluesky, Discord
webhooks). Three attempts at 1s/2s/4s + jitter.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

from . import scraper_reddit, scraper_rss, scraper_stocktwits, scraper_bluesky

logger = logging.getLogger(__name__)

T = TypeVar("T")


def with_retry(fn: Callable[[], T], *,
               max_attempts: int = 3,
               base_delay: float = 1.0,
               retry_on: tuple = (Exception,),
               description: str = "operation") -> T:
    """Retry `fn()` with exponential backoff. Re-raises the last exception if
    all attempts fail."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except retry_on as e:
            last_exc = e
            if attempt == max_attempts:
                logger.warning("%s failed after %d attempts: %r",
                               description, attempt, e)
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            logger.info("%s attempt %d/%d failed (%r); sleeping %.1fs",
                        description, attempt, max_attempts, e, delay)
            time.sleep(delay)
    # Unreachable, but keeps type-checkers happy.
    if last_exc:
        raise last_exc
    return None  # type: ignore[return-value]


def run_all() -> list[dict]:
    """Run every scraper and return one combined list of posts."""
    posts: list[dict] = []
    for name, fn in (
        ("reddit",     scraper_reddit.fetch),
        ("rss",        scraper_rss.fetch),
        ("stocktwits", scraper_stocktwits.fetch),
        ("bluesky",    scraper_bluesky.fetch),
    ):
        try:
            chunk = with_retry(fn, description=f"{name} fetch")
            posts.extend(chunk or [])
            logger.info("scraper %s returned %d posts", name, len(chunk or []))
        except Exception:
            logger.exception("scraper %s failed permanently; skipping", name)
    logger.info("scraper_manager: total %d posts", len(posts))
    return posts
