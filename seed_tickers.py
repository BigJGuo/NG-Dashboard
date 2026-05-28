"""NG-sector company-name → ticker dictionary and DB seeding entry point.

`COMPANY_NAME_TO_TICKER` is consumed by data/preprocessor.py when scanning post
text for ticker mentions that don't use cashtag syntax. Keys are lowercased on
load. Run this module as a script to materialise the watchlist into the DB:

    python seed_tickers.py
"""
from __future__ import annotations

import logging
import sys

import config

logger = logging.getLogger(__name__)


# Keys must be lowercase. Values are tickers from config.TICKERS_TO_TRACK.
# Both formal names and common short forms are included so cased-prose hits
# like "Cheniere" or "EQT Corp" both resolve to LNG / EQT.
COMPANY_NAME_TO_TICKER: dict[str, str] = {
    # NG ETFs
    "united states natural gas fund":  "UNG",
    "us natural gas fund":             "UNG",
    "u.s. natural gas fund":           "UNG",
    "ung etf":                         "UNG",
    "proshares ultra bloomberg natural gas": "BOIL",
    "boil etf":                        "BOIL",
    "proshares ultrashort bloomberg natural gas": "KOLD",
    "kold etf":                        "KOLD",
    "first trust natural gas etf":     "FCG",
    "fcg etf":                         "FCG",
    "united states 12 month natural gas fund": "UNL",

    # US E&P (gas-heavy)
    "eqt corporation":                 "EQT",
    "eqt corp":                        "EQT",
    "antero resources":                "AR",
    "range resources":                 "RRC",
    "southwestern energy":             "SWN",
    "coterra energy":                  "CTRA",
    "cabot oil and gas":               "CTRA",   # legacy name (merged into Coterra)
    "chesapeake energy":               "CHK",
    "matador resources":               "MTDR",
    "ovintiv":                         "OVV",
    "cnx resources":                   "CNX",
    "consol energy":                   "CNX",    # legacy
    "cabot oil & gas":                 "COG",

    # LNG / midstream LNG
    "cheniere energy":                 "LNG",
    "cheniere":                        "LNG",
    "cheniere energy partners":        "CQP",
    "new fortress energy":             "NFE",
    "tellurian":                       "TELL",

    # Pipelines / gas midstream
    "kinder morgan":                   "KMI",
    "williams companies":              "WMB",
    "williams cos":                    "WMB",
    "oneok":                           "OKE",
    "energy transfer":                 "ET",

    # NG macro / futures references — map to NG_FUTURES virtual bucket
    "henry hub":                       "NG_FUTURES",
    "ng futures":                      "NG_FUTURES",
    "natural gas futures":             "NG_FUTURES",
    "nymex ng":                        "NG_FUTURES",
    "front-month gas":                 "NG_FUTURES",
}


def seed() -> int:
    """Create the DB schema and ensure tracked tickers appear in any
    bookkeeping tables. Idempotent. Returns the count of tickers seeded."""
    from data import sentiment_db

    sentiment_db.init_db()
    logger.info("DB schema ensured at %s", sentiment_db.engine_url())
    logger.info("Tracking %d tickers: %s",
                len(config.TICKERS_TO_TRACK),
                ", ".join(config.TICKERS_TO_TRACK))
    logger.info("Company-name dictionary has %d entries",
                len(COMPANY_NAME_TO_TICKER))
    return len(config.TICKERS_TO_TRACK)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        n = seed()
        print(f"Seeded {n} tickers into the DB schema.")
    except Exception as e:
        logger.exception("Seed failed")
        sys.exit(1)
