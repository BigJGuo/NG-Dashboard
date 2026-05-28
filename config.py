"""Default configuration for the NG Trading Intelligence Dashboard.

All user-editable values (API key, refresh intervals, city selection, keywords)
can be overridden at runtime via the Settings modal and persisted to
dcc.Store(storage_type='local'). The values here are the defaults used on
first launch before the user touches anything.
"""

EIA_API_KEY_FALLBACK = "oMBzV4QQ9xCu2B9lIJYzYtL0wGxiau5hZ6QUhE1p"

EIA_STORAGE_URL = "https://api.eia.gov/v2/natural-gas/stor/wkly/data/"
EIA_PRODUCTION_URL = "https://api.eia.gov/v2/natural-gas/prod/sum/data/"
EIA_LNG_EXPORTS_URL = "https://api.eia.gov/v2/natural-gas/move/expc/data/"
EIA_CONSUMPTION_URL = "https://api.eia.gov/v2/natural-gas/cons/sum/data/"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
CFTC_LEGACY_URL = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"
CFTC_FINANCIAL_URL = "https://www.cftc.gov/files/dea/history/fut_fin_xls_{year}.zip"

DEFAULT_INTERVALS = {
    "clock_ms": 1000,
    "navbar_price_ms": 30 * 1000,
    "news_ms": 60 * 1000,
    "weather_ms": 15 * 60 * 1000,
    "storage_ms": 10 * 60 * 1000,
    "futures_ms": 30 * 1000,
    "cftc_ms": 60 * 60 * 1000,
    "trajectory_ms": 4 * 60 * 60 * 1000,
}

# region: NE, MW, S, W. weight: relative importance for NG demand (NE+MW = 1.5)
CITIES = [
    # ── Northeast (13) ──
    {"name": "Boston",         "lat": 42.36, "lon": -71.06, "region": "NE", "weight": 1.5},
    {"name": "New York City",  "lat": 40.71, "lon": -74.01, "region": "NE", "weight": 1.5},
    {"name": "Philadelphia",   "lat": 39.95, "lon": -75.17, "region": "NE", "weight": 1.5},
    {"name": "Baltimore",      "lat": 39.29, "lon": -76.61, "region": "NE", "weight": 1.5},
    {"name": "Washington DC",  "lat": 38.91, "lon": -77.04, "region": "NE", "weight": 1.5},
    {"name": "Pittsburgh",     "lat": 40.44, "lon": -79.99, "region": "NE", "weight": 1.5},
    {"name": "Buffalo",        "lat": 42.89, "lon": -78.87, "region": "NE", "weight": 1.5},
    {"name": "Rochester",      "lat": 43.16, "lon": -77.61, "region": "NE", "weight": 1.5},
    {"name": "Syracuse",       "lat": 43.05, "lon": -76.15, "region": "NE", "weight": 1.5},
    {"name": "Albany",         "lat": 42.65, "lon": -73.76, "region": "NE", "weight": 1.5},
    {"name": "Providence",     "lat": 41.82, "lon": -71.42, "region": "NE", "weight": 1.5},
    {"name": "Hartford",       "lat": 41.76, "lon": -72.68, "region": "NE", "weight": 1.5},
    {"name": "Portland ME",    "lat": 43.66, "lon": -70.26, "region": "NE", "weight": 1.5},

    # ── Midwest (16) ──
    {"name": "Chicago",        "lat": 41.85, "lon": -87.65, "region": "MW", "weight": 1.5},
    {"name": "Minneapolis",    "lat": 44.98, "lon": -93.27, "region": "MW", "weight": 1.5},
    {"name": "Detroit",        "lat": 42.33, "lon": -83.05, "region": "MW", "weight": 1.5},
    {"name": "Cleveland",      "lat": 41.50, "lon": -81.69, "region": "MW", "weight": 1.5},
    {"name": "Cincinnati",     "lat": 39.10, "lon": -84.51, "region": "MW", "weight": 1.5},
    {"name": "Columbus",       "lat": 39.96, "lon": -82.99, "region": "MW", "weight": 1.5},
    {"name": "Indianapolis",   "lat": 39.77, "lon": -86.16, "region": "MW", "weight": 1.5},
    {"name": "Milwaukee",      "lat": 43.04, "lon": -87.91, "region": "MW", "weight": 1.5},
    {"name": "Madison",        "lat": 43.07, "lon": -89.40, "region": "MW", "weight": 1.5},
    {"name": "Grand Rapids",   "lat": 42.96, "lon": -85.65, "region": "MW", "weight": 1.5},
    {"name": "Kansas City",    "lat": 39.10, "lon": -94.58, "region": "MW", "weight": 1.5},
    {"name": "St. Louis",      "lat": 38.63, "lon": -90.20, "region": "MW", "weight": 1.5},
    {"name": "Des Moines",     "lat": 41.59, "lon": -93.62, "region": "MW", "weight": 1.5},
    {"name": "Omaha",          "lat": 41.26, "lon": -95.93, "region": "MW", "weight": 1.5},
    {"name": "Fargo",          "lat": 46.87, "lon": -96.79, "region": "MW", "weight": 1.5},
    {"name": "Wichita",        "lat": 37.69, "lon": -97.34, "region": "MW", "weight": 1.0},

    # ── South (20) ──
    {"name": "Atlanta",        "lat": 33.75, "lon": -84.39, "region": "S",  "weight": 1.0},
    {"name": "Charlotte",      "lat": 35.23, "lon": -80.84, "region": "S",  "weight": 1.0},
    {"name": "Raleigh",        "lat": 35.78, "lon": -78.64, "region": "S",  "weight": 1.0},
    {"name": "Richmond",       "lat": 37.54, "lon": -77.44, "region": "S",  "weight": 1.0},
    {"name": "Nashville",      "lat": 36.17, "lon": -86.78, "region": "S",  "weight": 1.0},
    {"name": "Louisville",     "lat": 38.25, "lon": -85.76, "region": "S",  "weight": 1.0},
    {"name": "Memphis",        "lat": 35.15, "lon": -90.05, "region": "S",  "weight": 1.0},
    {"name": "Birmingham",     "lat": 33.53, "lon": -86.80, "region": "S",  "weight": 1.0},
    {"name": "Little Rock",    "lat": 34.74, "lon": -92.29, "region": "S",  "weight": 1.0},
    {"name": "New Orleans",    "lat": 29.95, "lon": -90.07, "region": "S",  "weight": 1.0},
    {"name": "Jacksonville",   "lat": 30.33, "lon": -81.66, "region": "S",  "weight": 1.0},
    {"name": "Orlando",        "lat": 28.54, "lon": -81.38, "region": "S",  "weight": 1.0},
    {"name": "Tampa",          "lat": 27.95, "lon": -82.46, "region": "S",  "weight": 1.0},
    {"name": "Miami",          "lat": 25.76, "lon": -80.19, "region": "S",  "weight": 1.0},
    {"name": "Houston",        "lat": 29.76, "lon": -95.37, "region": "S",  "weight": 1.0},
    {"name": "Dallas",         "lat": 32.78, "lon": -96.80, "region": "S",  "weight": 1.0},
    {"name": "Austin",         "lat": 30.27, "lon": -97.74, "region": "S",  "weight": 1.0},
    {"name": "San Antonio",    "lat": 29.42, "lon": -98.49, "region": "S",  "weight": 1.0},
    {"name": "El Paso",        "lat": 31.76, "lon": -106.49,"region": "S",  "weight": 1.0},
    {"name": "Oklahoma City",  "lat": 35.47, "lon": -97.52, "region": "S",  "weight": 1.0},
    {"name": "Tulsa",          "lat": 36.15, "lon": -95.99, "region": "S",  "weight": 1.0},

    # ── West (17) ──
    {"name": "Denver",         "lat": 39.74, "lon": -104.98,"region": "W",  "weight": 1.0},
    {"name": "Colorado Springs","lat": 38.83,"lon": -104.82,"region": "W",  "weight": 1.0},
    {"name": "Salt Lake City", "lat": 40.76, "lon": -111.89,"region": "W",  "weight": 1.0},
    {"name": "Albuquerque",    "lat": 35.08, "lon": -106.65,"region": "W",  "weight": 1.0},
    {"name": "Phoenix",        "lat": 33.45, "lon": -112.07,"region": "W",  "weight": 1.0},
    {"name": "Tucson",         "lat": 32.22, "lon": -110.97,"region": "W",  "weight": 1.0},
    {"name": "Las Vegas",      "lat": 36.17, "lon": -115.14,"region": "W",  "weight": 1.0},
    {"name": "Reno",           "lat": 39.53, "lon": -119.81,"region": "W",  "weight": 1.0},
    {"name": "Los Angeles",    "lat": 34.05, "lon": -118.24,"region": "W",  "weight": 1.0},
    {"name": "San Diego",      "lat": 32.72, "lon": -117.16,"region": "W",  "weight": 1.0},
    {"name": "San Francisco",  "lat": 37.77, "lon": -122.42,"region": "W",  "weight": 1.0},
    {"name": "Sacramento",     "lat": 38.58, "lon": -121.49,"region": "W",  "weight": 1.0},
    {"name": "San Jose",       "lat": 37.34, "lon": -121.89,"region": "W",  "weight": 1.0},
    {"name": "Portland OR",    "lat": 45.52, "lon": -122.68,"region": "W",  "weight": 1.0},
    {"name": "Seattle",        "lat": 47.61, "lon": -122.33,"region": "W",  "weight": 1.0},
    {"name": "Spokane",        "lat": 47.66, "lon": -117.43,"region": "W",  "weight": 1.0},
    {"name": "Boise",          "lat": 43.62, "lon": -116.21,"region": "W",  "weight": 1.0},
    {"name": "Billings",       "lat": 45.78, "lon": -108.50,"region": "W",  "weight": 1.0},
]

# Rough monthly normal high temps (°F). Stub data — refined values can replace later.
CLIMATE_NORMALS = {
    "NE": [35, 38, 47, 60, 70, 79, 84, 82, 75, 63, 52, 40],
    "MW": [32, 36, 47, 60, 71, 81, 85, 83, 76, 63, 49, 36],
    "S":  [55, 59, 67, 75, 82, 88, 91, 90, 85, 76, 66, 57],
    "W":  [45, 48, 55, 62, 72, 82, 89, 87, 80, 68, 55, 46],
}

# Approximate national 5-year seasonal residential+commercial demand range (Bcf/d) by month.
DEMAND_BAND_5Y = {
    1: (50, 90), 2: (45, 85), 3: (35, 65), 4: (20, 40), 5: (12, 25), 6: (8, 18),
    7: (8, 16), 8: (8, 16), 9: (10, 22), 10: (18, 38), 11: (32, 60), 12: (45, 80),
}

# ── Market-sentiment pipeline (NG-focused) ───────────────────────────────────
# RSS feeds: NG-native (always relevant) + broad financial (filtered by NG keywords/tickers
# in data/preprocessor.py before they reach FinBERT to save compute).
RSS_FEEDS = [
    # NG-native — bypass relevance filter
    ("RBN Energy",          "https://rbnenergy.com/feed"),
    ("EIA Today in Energy", "https://www.eia.gov/rss/todayinenergy.xml"),
    ("Rigzone",             "https://www.rigzone.com/news/rss/rigzone_latest.aspx"),
    ("Hart Energy",         "https://www.hartenergy.com/rss.xml"),
    # Broad financial — filtered by NG ticker or keyword presence
    ("Reuters Business",    "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"),
    ("Benzinga",            "https://feeds.benzinga.com/benzinga"),
    ("Seeking Alpha",       "https://seekingalpha.com/feed.xml"),
]

# Feed names in NG_NATIVE_FEEDS bypass the NG-relevance pre-filter.
NG_NATIVE_FEEDS = {"RBN Energy", "EIA Today in Energy", "Rigzone", "Hart Energy"}

# Subreddits scraped by data/scraper_reddit.py. NG-native subs bypass the
# relevance filter; general investing subs are filtered by ticker/keyword.
SUBREDDITS = [
    "energy", "naturalgas", "oil", "Commodities", "EnergyAndEnvironment",
    "wallstreetbets", "stocks", "investing",
]
NG_NATIVE_SUBREDDITS = {"energy", "naturalgas", "oil", "Commodities", "EnergyAndEnvironment"}

# NG ticker watchlist — what the sentiment dashboard tracks. NG_FUTURES is a
# virtual bucket for macro/futures posts that don't mention a specific equity.
TICKERS_TO_TRACK = [
    # NG ETFs
    "UNG", "BOIL", "KOLD", "FCG", "UNL",
    # US E&P (gas-heavy)
    "EQT", "AR", "RRC", "SWN", "CTRA", "CHK", "MTDR", "OVV", "CNX", "COG",
    # LNG / midstream LNG
    "LNG", "CQP", "NFE", "TELL",
    # Pipelines / gas midstream
    "KMI", "WMB", "OKE", "ET",
    # Virtual bucket for macro NG posts (futures, Henry Hub, no specific equity)
    "NG_FUTURES",
]
NG_MACRO_BUCKET = "NG_FUTURES"

# Keywords that mark a post as NG-relevant when no ticker is matched.
NG_RELEVANCE_KEYWORDS = {
    "natural gas", "nat gas", "natgas", "henry hub", "lng", "liquefied natural",
    "ng futures", "eia storage", "shale gas", "marcellus", "haynesville",
    "permian gas", "appalachia", "freeport lng", "sabine pass", "calcasieu",
    "corpus christi", "rig count", "freeze-off", "injection season",
    "withdrawal season", "polar vortex", "power burn", "ferc",
}

# Common all-caps words that look like cashtags but aren't tickers. Filtered
# from cashtag regex matches. Note "LNG" IS a real ticker (Cheniere) — when
# prefixed with $ in source text we keep it; when bare in body text without
# $, it's treated as the English noun.
TICKER_STOPLIST = {
    "CEO", "USA", "GDP", "FED", "API", "IPO", "ETF", "FOMC", "AI", "EV",
    "USD", "GBP", "EUR", "OPEC", "EIA", "DOE", "FBI", "NYSE", "NASDAQ",
    "EPS", "PE", "PT", "MA", "RSI", "ATH",
}

# FinBERT / scoring knobs.
MIN_CONFIDENCE_THRESHOLD = 0.60
ENGAGEMENT_FILTER_REDDIT_SCORE = 5
ENGAGEMENT_FILTER_REDDIT_COMMENTS = 3
SCRAPE_INTERVAL_MINUTES = 15
SENTIMENT_DECAY_LAMBDA = 0.1
BATCH_SIZE_FINBERT = 32
FINBERT_MODEL_NAME = "ProsusAI/finbert"

# Alert thresholds.
ALERT_COMPOSITE_THRESHOLD = 70.0       # |composite_signal| > 70 fires
ALERT_VELOCITY_THRESHOLD = 30.0        # |sentiment_velocity| > 30 in 1h fires
ALERT_VOLUME_MULTIPLIER = 3.0          # mention_volume > 3 * 7d_mean fires
ALERT_DEDUP_MINUTES = 60               # don't re-fire same (ticker,trigger) within 60min

# Dashboard refresh.
SENTIMENT_DASHBOARD_REFRESH_MS = 60 * 1000

# Pruning.
RAW_POST_RETENTION_DAYS = 30


# ── End-of-Season Trajectory Model ─────────────────────────────────────────────

# Analyst rule-of-thumb minimum comfortable storage levels (Bcf).
# 3,200 Bcf entering winter is the level below which most winter-supply analysts
# flag price upside risk. 1,400 Bcf coming out of withdrawal season is the level
# below which spring-injection-season tightness gets called out.
EOS_MIN_COMFORTABLE_NOV1 = 3200
EOS_MIN_COMFORTABLE_APR1 = 1400

HDD_DEMAND_COEFFICIENT = 0.045  # Bcf/d per national HDD (matches data/weather.py)

# Cooling-degree-day (CDD) → power-burn demand. Gas-fired generation responds
# strongly to summer heat. ~0.12 Bcf/d per national CDD is the industry rule
# of thumb (a hot day with ~25 national CDDs ≈ +3 Bcf/d of power-burn demand).
CDD_DEMAND_COEFFICIENT = 0.12

WEATHER_CONFIDENCE_DECAY_START_WEEK = 2
WEATHER_CONFIDENCE_DECAY_PER_WEEK = 0.15
WEATHER_CONFIDENCE_DECAY_FLOOR_WEEK = 6

INJECTION_SEASON_START_MONTH_DAY = (4, 1)   # Apr 1
INJECTION_SEASON_END_MONTH_DAY   = (10, 31) # Oct 31 — trajectory target = Nov 1


def _build_national_degree_day_normals_by_week():
    """Build length-52 lists of national HDD and CDD climate normals per ISO week.

    Stub: derived from monthly high-temp CLIMATE_NORMALS by region. Mean daily
    temp is approximated as (high - 15)F. HDD = max(65 - mean, 0); CDD =
    max(mean - 65, 0). Regions are weighted by city count × demand-weight
    (NE/MW carry 1.5×). Replace with true NOAA 30-year normals when available.
    """
    region_city_count = {"NE": 0, "MW": 0, "S": 0, "W": 0}
    region_weight = {"NE": 1.5, "MW": 1.5, "S": 1.0, "W": 1.0}
    for c in CITIES:
        region_city_count[c["region"]] += 1
    total_weight = sum(region_city_count[r] * region_weight[r] for r in region_city_count)
    weekly_hdd, weekly_cdd = [], []
    for week in range(1, 53):
        month_idx = min(int((week - 1) / (52 / 12)), 11)  # 0-11
        national_mean_temp = 0.0
        for region, normals in CLIMATE_NORMALS.items():
            mean_f = normals[month_idx] - 15
            region_w = region_city_count[region] * region_weight[region]
            national_mean_temp += mean_f * region_w
        national_mean_temp /= total_weight
        weekly_hdd.append(round(max(65 - national_mean_temp, 0.0), 1))
        weekly_cdd.append(round(max(national_mean_temp - 65, 0.0), 1))
    return weekly_hdd, weekly_cdd


NATIONAL_HDD_NORMALS_BY_WEEK, NATIONAL_CDD_NORMALS_BY_WEEK = \
    _build_national_degree_day_normals_by_week()


# ── Regional Storage Breakdown ─────────────────────────────────────────────────

# EIA storage regions (used by the choropleth on the Storage tab). State lists
# follow EIA's published region definitions.
STORAGE_REGION_STATES = {
    "East":          ['CT', 'DC', 'DE', 'FL', 'GA', 'MA', 'MD', 'ME', 'MI',
                      'NC', 'NH', 'NJ', 'NY', 'OH', 'PA', 'RI', 'SC', 'VA',
                      'VT', 'WV'],
    "Midwest":       ['IA', 'IL', 'IN', 'KS', 'KY', 'MN', 'MO', 'ND', 'NE',
                      'SD', 'TN', 'WI'],
    "Mountain":      ['AZ', 'CO', 'ID', 'MT', 'NM', 'NV', 'UT', 'WY'],
    "Pacific":       ['AK', 'CA', 'HI', 'OR', 'WA'],
    "South Central": ['AL', 'AR', 'LA', 'MS', 'OK', 'TX'],
}
STATE_TO_STORAGE_REGION = {
    st: region for region, sts in STORAGE_REGION_STATES.items() for st in sts
}

# Dynamic refresh cadence for regional storage. EIA publishes weekly storage
# Thursdays ~10:30am ET; refresh fast during that window, slow otherwise.
REGIONAL_STORAGE_FAST_MS = 60 * 1000
REGIONAL_STORAGE_SLOW_MS = 10 * 60 * 1000


# ── Legacy news-tab compatibility shims ──────────────────────────────────────
# The news tab and Settings modal still reference these names. They map to the
# new sentiment-pipeline structures so the old UI continues to work alongside
# the new FinBERT/Reddit pipeline.

KEYWORDS = sorted(NG_RELEVANCE_KEYWORDS)

BULLISH_WORDS = {"freeze-off", "draw", "cold", "outage", "force majeure",
                 "curtailment", "disruption", "beat", "polar vortex", "shortage"}
BEARISH_WORDS = {"mild", "warm", "injection", "build", "surplus", "miss",
                 "oversupply", "glut"}

TOPIC_BUCKETS = {
    "Storage":     ["storage", "injection", "withdrawal", "eia report",
                    "storage miss", "storage beat"],
    "Weather":     ["polar vortex", "cold", "warm", "mild", "freeze",
                    "heat wave", "temperature"],
    "LNG Exports": ["lng", "freeport", "sabine pass", "corpus christi",
                    "calcasieu pass", "export terminal", "regasification"],
    "Pipeline Infrastructure": ["pipeline", "algonquin", "transco", "ferc",
                                "capacity constraint", "force majeure"],
    "Production":  ["production curtailment", "freeze-off", "rig", "drilling",
                    "wellhead"],
    "Demand":      ["power burn", "industrial demand", "residential heating",
                    "gas demand"],
    "Regulatory":  ["ferc", "regulation", "permit", "approval"],
}

NEWS_FLASH_WINDOW_MINUTES = 10
NEWS_FLASH_THRESHOLD = 3

