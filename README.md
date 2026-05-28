# NG Trading Intelligence Dashboard

A unified natural-gas trading intelligence dashboard built as a single Plotly Dash application. Five tabs cover the inputs that move the front of the curve: **Storage**, **Weather**, **News**, **Positioning**, **Curve**. The **News** tab runs a full FinBERT sentiment pipeline (Reddit + RSS + StockTwits + Bluesky) scoped to NG-relevant tickers.

## Quick start

1. **Install dependencies**

   ```powershell
   pip install -r requirements.txt
   ```

2. **Set up secrets**

   Copy `.env.example` → `.env` and fill in what you have. Reddit credentials are optional — without them the Reddit scraper falls back to the public `old.reddit.com/.json` endpoint. Discord webhooks are optional.

   ```
   REDDIT_CLIENT_ID=...
   REDDIT_CLIENT_SECRET=...
   REDDIT_USER_AGENT=NGSentimentBot/1.0 (by /u/your_handle)
   DISCORD_WEBHOOK_URL=         # optional
   DB_URL=sqlite:///data/sentiment.db
   USE_GPU=false
   ```

3. **Get a free EIA API key** at <https://www.eia.gov/opendata/register.php>, then launch the app and paste it into the **Settings** modal (top-right of the navbar). Settings persist in `localStorage` and survive browser refreshes.

4. **Run**

   ```powershell
   python app.py
   ```

   First launch downloads the FinBERT weights (~440 MB) into the HuggingFace cache. Subsequent runs are instant. Open <http://127.0.0.1:8055> in your browser.

## Layout

```
app.py                       # entry point + navbar + tab routing + snapshot
config.py                    # tickers, subreddits, feeds, NG keywords, knobs
scheduler.py                 # APScheduler jobs (15m / 1h / 6h)
seed_tickers.py              # NG company-name → ticker dictionary
.env.example                 # template for secrets

tabs/{storage,weather,news,positioning,curve}.py
data/{eia,weather,cftc,futures,trajectory,alerts}.py            # existing NG sources

# Sentiment pipeline (NG-focused)
data/sentiment_db.py         # SQLAlchemy ORM + session manager
data/finbert_scorer.py       # singleton FinBERT loader + batched inference
data/preprocessor.py         # text cleaning, ticker extraction, NG relevance
data/scraper_reddit.py       # PRAW + public-JSON fallback
data/scraper_rss.py          # feedparser (NG + financial feeds)
data/scraper_stocktwits.py   # cashtag streams (Twitter replacement)
data/scraper_bluesky.py      # AT Protocol public searchPosts
data/scraper_manager.py      # orchestration + retry helper
data/signal_aggregator.py    # per-window composite + velocity
data/alerts_sentiment.py     # threshold alerts → DB + log + Discord

utils/{theme,snapshot}.py
assets/custom.css
logs/sentiment.log           # rotating, gitignored
data/sentiment.db            # SQLite, gitignored
```

Each tab module exports `layout()` and `register_callbacks(app)`. The News tab reads exclusively from the sentiment DB; scraping/scoring happen in `scheduler.py` background jobs.

## How the sentiment pipeline works

The pipeline is **NG-focused throughout** — the watchlist is gas-sector tickers (UNG, EQT, LNG, KMI, …), not S&P 500 names. Non-native sources (Reuters, Benzinga, Seeking Alpha, /r/wallstreetbets, Bluesky) are filtered against an NG-keyword list and ticker set before reaching FinBERT, so compute isn't burned on irrelevant chatter.

```
                  ┌───────────────────────┐
                  │   APScheduler 15-min  │
                  └───────────┬───────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
   scraper_reddit       scraper_rss          scraper_stocktwits, scraper_bluesky
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              ▼
                preprocessor.process(...)
                ├── clean text
                ├── extract tickers (cashtag regex + company-name dict)
                ├── NG-relevance filter (keep if ticker OR keyword)
                └── dedup by URL
                              ▼
              finbert_scorer.score_batch([texts])
                  positive / negative / neutral / confidence
                              ▼
              sentiment_db.insert_post + insert_scores
              (only confidence ≥ 0.60 persisted)
                              ▼
            signal_aggregator.update_window('1h')
              composite = 0.5·sentiment + 0.3·bull_bear + 0.2·vol_pct
                              ▼
            alerts_sentiment.check_thresholds('1h')
              composite>70, |velocity|>30, volume>3×7d-mean
                              ▼
              dashboard reads via tabs/news.py + nav callbacks
```

Hourly + 6-hourly jobs roll the 4h and 24h aggregates and prune raw posts older than 30 days.

## Settings

Click **Settings** in the top-right navbar:
- EIA API key
- Weather refresh interval (minutes)
- Cities to include in weather/demand panels
- Sentiment tickers (one per line)

Values are saved to `dcc.Store(storage_type='local')`. Sentiment-ticker changes take effect after a restart (the scheduler caches the watchlist).

## Snapshot PDF

Click **Snapshot** in the top-right navbar to download a landscape PDF containing the current state of every chart across all five tabs. Rendered with `kaleido` (charts) + `reportlab` (assembly). The News tab snapshot now pulls from the sentiment DB directly — no in-memory store required.

## Data sources

| Tab | Source | Refresh |
|---|---|---|
| Storage | EIA v2 API — `natural-gas/stor/wkly` | 10 min |
| Weather | Open-Meteo (no key required) | 10 min |
| News    | Reddit (PRAW or public JSON) · RSS (RBN, EIA, Rigzone, Hart, Reuters, Benzinga, Seeking Alpha) · StockTwits public streams · Bluesky public `searchPosts` | 15 min (background) + 60 s (UI auto-refresh) |
| Positioning | CFTC COT legacy futures-only / financial futures (auto-fallback) | weekly |
| Curve | yfinance — NYMEX NG futures `NG=F`, `NG{M}{YY}.NYM` | 30 s |

### NG-relevant tickers tracked

NG ETFs: UNG, BOIL, KOLD, FCG, UNL
US E&P (gas-heavy): EQT, AR, RRC, SWN, CTRA, CHK, MTDR, OVV, CNX, COG
LNG: LNG, CQP, NFE, TELL
Pipelines / midstream: KMI, WMB, OKE, ET
Virtual macro bucket: NG_FUTURES (Henry Hub, NYMEX NG futures, generic NG macro posts)

## Alerts

The sentiment pipeline fires alerts when:
- composite signal crosses ±70 (strong),
- 1h velocity exceeds ±30 points (rapid shift), or
- mention volume exceeds 3× the 7-day rolling mean.

Each alert is logged to `logs/sentiment.log`, written to the `alerts` DB table (read by the navbar pill), and — if `DISCORD_WEBHOOK_URL` is set — posted to Discord with 3-retry exponential backoff. In-memory dedup prevents the same `(ticker, trigger)` from firing more than once per hour.

## Troubleshooting

- **FinBERT first-run is slow or fails to download**: the ~440 MB HuggingFace download needs network access to `huggingface.co`. If the download is interrupted, delete `~/.cache/huggingface/hub/models--ProsusAI--finbert/` and re-launch.
- **CUDA OOM or torch import error**: set `USE_GPU=false` in `.env` to force CPU; FinBERT runs comfortably on CPU at the 15-min cadence.
- **Reddit returns no posts**: without `REDDIT_CLIENT_ID/SECRET` we fall back to public `old.reddit.com/.json` which can rate-limit aggressively. Register a free script app at <https://www.reddit.com/prefs/apps>.
- **StockTwits 404 for NG_F**: not every continuous-futures symbol is quoted on StockTwits; the scraper logs and skips.
- **Bluesky cold start**: the public AT Protocol search has been intermittently rate-limited in the past — failures degrade gracefully without taking the run down.
- **`kaleido` fails on Windows / `Snapshot` returns empty**: pinned to `kaleido==0.2.1` because newer 0.4.x has a known Windows subprocess bug.
- **`yfinance` returns NaN for back-month tickers**: outer-month NG tickers (e.g. `NGZ26.NYM`) are not always quoted on Yahoo; the curve chart skips NaNs.

## Running on a Mac / Linux

The app is portable — replace `powershell` with your shell of choice. PyTorch, Plotly Dash, kaleido 0.2.1, and reportlab all install cleanly on macOS and Linux. For GPU acceleration on Linux/CUDA, install the matching `torch` wheel for your CUDA version before running `pip install -r requirements.txt`.

## Architecture notes

- All theming flows through `utils/theme.plotly_layout()` — no tab redefines colors.
- Sentiment data flows through SQLite (or Postgres via `DB_URL`), not `dcc.Store`. APScheduler writes; Dash reads.
- All timestamps are stored as UTC and rendered in local time.
- Every `data/*.py` network call is wrapped in `try/except` and either returns an empty result on failure or logs and continues. Per-source failure does not kill the 15-minute cycle.
