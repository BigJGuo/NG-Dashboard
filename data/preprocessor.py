"""Text cleaning + ticker extraction + NG-relevance pre-filter.

Pipeline contract: a normalised post dict from scraper_manager.run_all() looks
like:

    {
        "source":         "reddit" | "news" | "stocktwits" | "bluesky",
        "source_name":    "r/naturalgas" | "RBN Energy" | "$UNG" | ...,
        "text":           "<title>\n<body>",
        "url":            "https://...",
        "author":         "<username>" | None,
        "engagement_score": float,
        "created_at":     datetime (tz-aware UTC),
        "is_ng_native":   True if source bypasses the NG-relevance filter
    }

`process()` enriches each dict with:

    - tickers: list[str] of matched watchlist tickers (may include NG_FUTURES)
    - cleaned_text: text suitable for FinBERT (max 512 tokens worth of chars)

and drops posts that don't pass the NG-relevance filter.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Iterable

import config
from seed_tickers import COMPANY_NAME_TO_TICKER

logger = logging.getLogger(__name__)

_CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5})\b")
_URL_RE     = re.compile(r"https?://\S+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE      = re.compile(r"\s+")

_TICKER_SET = {t.upper() for t in config.TICKERS_TO_TRACK}
_STOPLIST   = {s.upper() for s in config.TICKER_STOPLIST}
_NG_KW_LOWER = {k.lower() for k in config.NG_RELEVANCE_KEYWORDS}
_COMPANY_DICT_LOWER = {k.lower(): v for k, v in COMPANY_NAME_TO_TICKER.items()}

# Pre-compile a single alternation for company-name matching so we do one pass
# across the text rather than one substring search per dict entry.
_COMPANY_NAME_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(k) for k in _COMPANY_DICT_LOWER),
                             key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
) if _COMPANY_DICT_LOWER else None


# ── Public API ───────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = _HTML_TAG_RE.sub(" ", t)
    t = _URL_RE.sub(" ", t)
    t = t.replace("&amp;", "&").replace("&nbsp;", " ").replace("\r", " ")
    t = _WS_RE.sub(" ", t).strip()
    # FinBERT cap is 512 tokens; ~4 chars/token average + safety → 1800 chars.
    if len(t) > 1800:
        t = t[:1800]
    return t


def extract_tickers(text: str) -> list[str]:
    """Return watchlist tickers found in `text`. Cashtags ($XYZ) are matched
    case-insensitive; company names are matched word-bounded case-insensitive.
    Duplicates collapsed. Stoplist removed."""
    if not text:
        return []
    found: set[str] = set()

    for m in _CASHTAG_RE.finditer(text):
        sym = m.group(1).upper()
        if sym in _STOPLIST:
            continue
        if sym in _TICKER_SET:
            found.add(sym)

    if _COMPANY_NAME_RE is not None:
        for m in _COMPANY_NAME_RE.finditer(text):
            name = m.group(1).lower()
            ticker = _COMPANY_DICT_LOWER.get(name)
            if ticker and ticker.upper() in _TICKER_SET:
                found.add(ticker.upper())

    return sorted(found)


def has_ng_keyword(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(kw in low for kw in _NG_KW_LOWER)


def is_ng_relevant(text: str) -> bool:
    """A bare-text relevance check exposed for tests / callers without a full
    post dict. Considered relevant if it mentions a watchlist ticker OR an NG
    keyword."""
    if not text:
        return False
    if extract_tickers(text):
        return True
    return has_ng_keyword(text)


def process(posts: Iterable[dict]) -> list[dict]:
    """Run the full preprocessing pipeline. Returns the surviving posts with
    `tickers` and `cleaned_text` populated; drops non-NG-relevant posts and
    deduplicates by URL within the batch."""
    out: list[dict] = []
    seen_urls: set[str] = set()
    dropped_irrelevant = 0
    dropped_dup = 0

    for p in posts:
        text = clean_text(p.get("text") or "")
        if not text:
            continue

        tickers = extract_tickers(text)
        ng_native = bool(p.get("is_ng_native"))
        is_relevant = ng_native or bool(tickers) or has_ng_keyword(text)
        if not is_relevant:
            dropped_irrelevant += 1
            continue

        # If relevant but no explicit ticker, bucket under NG_FUTURES (the
        # macro/futures virtual ticker).
        if not tickers:
            tickers = [config.NG_MACRO_BUCKET]

        url = p.get("url")
        if url and url in seen_urls:
            dropped_dup += 1
            continue
        if url:
            seen_urls.add(url)

        enriched = dict(p)
        enriched["text"] = text         # overwrite with cleaned text
        enriched["cleaned_text"] = text
        enriched["tickers"] = tickers
        out.append(enriched)

    logger.info("preprocess: %d in → %d kept (dropped %d irrelevant, %d dup)",
                len(out) + dropped_irrelevant + dropped_dup, len(out),
                dropped_irrelevant, dropped_dup)
    return out
