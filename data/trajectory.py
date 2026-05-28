"""End-of-season storage trajectory model.

This module is the data + math backbone for the Storage tab's
"End-of-Season Storage Trajectory Model" panel. It is intentionally pure-Python
(no Dash imports here) so it can be smoke-tested standalone.

Pipeline:
    1. fetch_eia_storage_history()        — weekly working gas, 1994+
    2. fetch_eia_production()             — monthly dry-gas prod, forward-filled
    3. fetch_lng_exports()                — monthly LNG exports, /30 → Bcf/d
    4. calculate_historical_seasonal_patterns() — per-ISO-week stats (10y / 3y)
    5. calculate_production_adjustment()  — 4-wk avg vs 3-yr same-week → Bcf/wk
    6. calculate_lng_adjustment()         — same, sign-flipped
    7. calculate_weather_adjustment()     — HDD-driven, with confidence decay
    8. build_trajectory_scenarios()       — base/bull/bear + confidence band
    9. calculate_end_of_season_targets()  — 5y avg/max/min/min-comfortable
   10. compute_trajectory()               — top-level orchestrator
   11. SQLite accuracy tracking          — log_model_prediction, etc.

Run as a script for a live smoke test:
    python -m data.trajectory
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from typing import Any

import numpy as np
import pandas as pd
import requests

import config
from data import eia

DB_PATH = os.path.join(os.path.dirname(__file__), "model_accuracy.db")


# ── EIA fetch wrappers ─────────────────────────────────────────────────────────


def fetch_eia_storage_history(api_key: str) -> pd.DataFrame:
    """Pull weekly working gas in underground storage going back to 1994.

    Wraps data.eia.fetch_storage_weekly but requests up to 2,000 rows so we get
    the full 30-year history needed for 10-year seasonal pattern stats. Returns
    a DataFrame indexed by period (date) with column 'value_bcf'.
    """
    if not api_key:
        return pd.DataFrame()
    params = {
        "api_key": api_key,
        "frequency": "weekly",
        "data[0]": "value",
        "facets[duoarea][]": "R48",
        "facets[process][]": "SWO",
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "offset": 0,
        "length": 2000,
    }
    try:
        r = requests.get(config.EIA_STORAGE_URL, params=params, timeout=30)
        r.raise_for_status()
        rows = r.json().get("response", {}).get("data", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["period"] = pd.to_datetime(df["period"])
        df["value_bcf"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value_bcf"]).sort_values("period")
        return df[["period", "value_bcf"]].set_index("period")
    except Exception:
        return pd.DataFrame()


def fetch_eia_production(api_key: str) -> pd.DataFrame:
    """Pull monthly U.S. dry-gas production (Bcf/d) and forward-fill to weekly.

    EIA's public API only publishes monthly dry-gas production (NG.N9070US2.M).
    To meet the spec's "weekly with 2-week lag forward-fill" requirement we:
      1. Fetch the monthly series and convert to Bcf/d.
      2. Resample to weekly (W-THU to align with EIA storage Thursday cadence),
         forward-fill missing weeks within each month.
      3. For the most recent 2 weeks, extrapolate using a linear trend computed
         from the prior 8 weeks (per spec).
    Returns DataFrame indexed by week with column 'production_bcfd'.
    """
    if not api_key:
        return pd.DataFrame()
    params = {
        "api_key": api_key,
        "frequency": "monthly",
        "data[0]": "value",
        "facets[series][]": "N9070US2",
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "offset": 0,
        "length": 600,
    }
    try:
        r = requests.get(config.EIA_PRODUCTION_URL, params=params, timeout=30)
        r.raise_for_status()
        rows = r.json().get("response", {}).get("data", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["period"] = pd.to_datetime(df["period"])
        df["production_mmcf_per_month"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["production_mmcf_per_month"]).sort_values("period")
        days_in_month = df["period"].dt.days_in_month
        df["production_bcfd"] = df["production_mmcf_per_month"] / 1000.0 / days_in_month
        weekly = (
            df.set_index("period")["production_bcfd"]
            .resample("W-THU").ffill()
            .to_frame()
        )
        if len(weekly) >= 10:
            last_idx = weekly.index[-1]
            recent = weekly["production_bcfd"].iloc[-10:-2].values
            if len(recent) >= 4 and not np.isnan(recent).all():
                x = np.arange(len(recent))
                slope, intercept = np.polyfit(x, recent, 1)
                for offset in (-1, 0):
                    wk_idx = last_idx + dt.timedelta(weeks=offset + 1)
                    if wk_idx not in weekly.index:
                        weekly.loc[wk_idx, "production_bcfd"] = (
                            intercept + slope * (len(recent) + offset)
                        )
        weekly = weekly.sort_index()
        return weekly
    except Exception:
        return pd.DataFrame()


def fetch_eia_consumption(api_key: str) -> pd.DataFrame:
    """Pull EIA monthly U.S. total natural gas consumption (NG.N9140US2.M).

    This is domestic consumption only (residential + commercial + industrial +
    electric power + vehicle fuel) — does NOT include LNG exports, so no
    double-counting with fetch_lng_exports.

    Returns DataFrame indexed by week (W-THU) with column 'consumption_bcfd',
    monthly→weekly forward-filled.
    """
    if not api_key:
        return pd.DataFrame()
    params = {
        "api_key": api_key,
        "frequency": "monthly",
        "data[0]": "value",
        "facets[series][]": "N9140US2",
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "offset": 0,
        "length": 600,
    }
    try:
        r = requests.get(config.EIA_CONSUMPTION_URL, params=params, timeout=30)
        r.raise_for_status()
        rows = r.json().get("response", {}).get("data", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["period"] = pd.to_datetime(df["period"])
        df["cons_mmcf_per_month"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["cons_mmcf_per_month"]).sort_values("period")
        days_in_month = df["period"].dt.days_in_month
        df["consumption_bcfd"] = df["cons_mmcf_per_month"] / 1000.0 / days_in_month
        weekly = (
            df.set_index("period")["consumption_bcfd"]
            .resample("W-THU").ffill()
            .to_frame()
        )
        return weekly.sort_index()
    except Exception:
        return pd.DataFrame()


def fetch_lng_exports(api_key: str) -> pd.DataFrame:
    """Pull EIA monthly LNG exports (NG.N9133US2.M), convert to daily Bcf/d.

    Per user direction, scraping individual terminal nominations is not feasible
    against the literal targets named in the spec (no public scrapeable pages),
    so we use the EIA monthly series as the single source. To plug in a real
    feedgas feed later, add a scraper above this function and merge its results
    before the EIA fallback.

    Returns DataFrame indexed by week (W-THU) with column 'lng_exports_bcfd',
    covering the full available history (EIA series begins ~2016).
    """
    if not api_key:
        return pd.DataFrame()
    params = {
        "api_key": api_key,
        "frequency": "monthly",
        "data[0]": "value",
        "facets[series][]": "N9133US2",
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "offset": 0,
        "length": 600,
    }
    try:
        r = requests.get(config.EIA_LNG_EXPORTS_URL, params=params, timeout=30)
        r.raise_for_status()
        rows = r.json().get("response", {}).get("data", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["period"] = pd.to_datetime(df["period"])
        df["lng_mmcf_per_month"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["lng_mmcf_per_month"]).sort_values("period")
        days_in_month = df["period"].dt.days_in_month
        df["lng_exports_bcfd"] = df["lng_mmcf_per_month"] / 1000.0 / days_in_month
        weekly = (
            df.set_index("period")["lng_exports_bcfd"]
            .resample("W-THU").ffill()
            .to_frame()
        )
        return weekly.sort_index()
    except Exception:
        return pd.DataFrame()


# ── Statistical patterns ───────────────────────────────────────────────────────


def calculate_historical_seasonal_patterns(storage_df: pd.DataFrame) -> pd.DataFrame:
    """For each ISO week 1-52, compute weekly-net-change stats over 10y and 3y.

    Returns a DataFrame indexed by week with columns:
        mean_{w}, median_{w}, std_{w},
        p10_{w}, p25_{w}, p75_{w}, p90_{w},
        mean_bottom25_{w}, mean_top25_{w}
    for w in {'10y', '3y'}. The bottom25/top25 columns are true conditional
    means of the lower-quartile (values ≤ p25) and upper-quartile (values ≥ p75)
    samples — used by build_trajectory_scenarios for bull/bear lines.
    """
    if storage_df.empty:
        return pd.DataFrame()
    changes = storage_df["value_bcf"].diff().dropna()
    if changes.empty:
        return pd.DataFrame()
    df = changes.to_frame("change")
    df["week"] = df.index.isocalendar().week
    df["year"] = df.index.year
    current_year = int(df["year"].max())

    def _mean_bottom_quartile(s: pd.Series) -> float:
        q = s.quantile(0.25)
        sel = s[s <= q]
        return float(sel.mean()) if not sel.empty else float("nan")

    def _mean_top_quartile(s: pd.Series) -> float:
        q = s.quantile(0.75)
        sel = s[s >= q]
        return float(sel.mean()) if not sel.empty else float("nan")

    def _stats_for(window_years: int, suffix: str) -> pd.DataFrame:
        cutoff = current_year - window_years
        windowed = df[(df["year"] >= cutoff) & (df["year"] < current_year)]
        if windowed.empty:
            return pd.DataFrame()
        g = windowed.groupby("week")["change"]
        return pd.DataFrame({
            f"mean_{suffix}":          g.mean(),
            f"median_{suffix}":        g.median(),
            f"std_{suffix}":           g.std().fillna(0),
            f"p10_{suffix}":           g.quantile(0.10),
            f"p25_{suffix}":           g.quantile(0.25),
            f"p75_{suffix}":           g.quantile(0.75),
            f"p90_{suffix}":           g.quantile(0.90),
            f"mean_bottom25_{suffix}": g.apply(_mean_bottom_quartile),
            f"mean_top25_{suffix}":    g.apply(_mean_top_quartile),
        })

    s10 = _stats_for(10, "10y")
    s3 = _stats_for(3, "3y")
    if s10.empty and s3.empty:
        return pd.DataFrame()
    if s10.empty:
        return s3
    if s3.empty:
        return s10
    return s10.join(s3, how="outer")


# ── Adjustment calculations ────────────────────────────────────────────────────


def _structural_growth_bcfd(series: pd.Series, years_back: int = 3) -> float:
    """Trailing-12-month avg of `series` now vs trailing-12-month avg N years ago.

    All three structural drivers (production, LNG, consumption) come from
    monthly EIA series with ~2-3 month reporting lag. A simple 4-week tail
    comparison against same-ISO-week prior years gives misleading results
    because the latest data point is from a different season than the
    projection horizon. Trailing-12-month averages strip out seasonality and
    isolate the true structural growth.
    """
    if series.empty or len(series) < 60:
        return 0.0
    series = series.dropna().sort_index()
    last = series.index[-1]
    cur_window = series[series.index > last - pd.DateOffset(months=12)]
    prev_end = last - pd.DateOffset(years=years_back)
    prev_start = prev_end - pd.DateOffset(months=12)
    prev_window = series[(series.index > prev_start) & (series.index <= prev_end)]
    if cur_window.empty or prev_window.empty:
        return 0.0
    return float(cur_window.mean() - prev_window.mean())


def calculate_production_adjustment(prod_df: pd.DataFrame,
                                    storage_df: pd.DataFrame) -> float:
    """Trailing-12mo production growth vs 3yr prior trailing-12mo, in Bcf/week.

    Returns (current_12mo_avg - 3y_ago_12mo_avg) × 7. Positive when production
    has grown structurally — adds to weekly injection estimate.
    """
    if prod_df.empty:
        return 0.0
    return _structural_growth_bcfd(prod_df["production_bcfd"]) * 7.0


def calculate_lng_adjustment(lng_df: pd.DataFrame,
                             storage_df: pd.DataFrame) -> float:
    """Trailing-12mo LNG export growth vs 3yr prior, in Bcf/week (sign-flipped).

    Higher LNG = more gas leaving the system = LESS injection, so the return
    value is negative when LNG has grown.
    """
    if lng_df.empty:
        return 0.0
    return -_structural_growth_bcfd(lng_df["lng_exports_bcfd"]) * 7.0


def calculate_demand_adjustment(cons_df: pd.DataFrame,
                                storage_df: pd.DataFrame) -> float:
    """Trailing-12mo domestic-consumption growth vs 3yr prior, Bcf/wk (sign-flipped).

    Returns -(current_12mo_avg - 3y_ago_12mo_avg) × 7. Higher domestic demand
    means LESS gas to inject into storage. Captures structural demand growth
    (power burn, industrial, res/comm) that the production adjustment alone
    misses. Uses a 12-month rolling window so the answer is calendar-neutral
    — winter-month consumption growth doesn't get applied wholesale to summer
    projection weeks.
    """
    if cons_df.empty:
        return 0.0
    return -_structural_growth_bcfd(cons_df["consumption_bcfd"]) * 7.0


    """Weekly Bcf demand adjustment from HDD + CDD vs climate normal.

    Args:
        weather_forecast_df: DataFrame indexed by forecast date with columns
            'national_hdd' and 'national_cdd' (national daily figures).
            10-day window expected.

    Returns:
        Series indexed by week-ending date (W-THU). Negative when net hotter
        OR colder than normal (both increase total gas demand and reduce
        injection). For weeks beyond the 10-day window the series carries
        zeros — caller falls back to historical seasonal baseline.

        Confidence-decay schedule: weeks 0-2 → full impact, then -15% per
        week, zero at week 6+.
    """
    if weather_forecast_df.empty:
        return pd.Series(dtype=float)
    if "national_hdd" not in weather_forecast_df.columns:
        return pd.Series(dtype=float)
    df = weather_forecast_df.copy()
    df.index = pd.to_datetime(df.index)
    df["week"] = df.index.isocalendar().week
    df["hdd_normal"] = df["week"].map(
        lambda w: config.NATIONAL_HDD_NORMALS_BY_WEEK[(int(w) - 1) % 52]
    )
    df["cdd_normal"] = df["week"].map(
        lambda w: config.NATIONAL_CDD_NORMALS_BY_WEEK[(int(w) - 1) % 52]
    )
    df["hdd_dev"] = df["national_hdd"] - df["hdd_normal"]
    cdd_series = df.get("national_cdd", pd.Series(0.0, index=df.index)).fillna(0.0)
    df["cdd_dev"] = cdd_series - df["cdd_normal"]
    df["demand_bcfd"] = (df["hdd_dev"] * config.HDD_DEMAND_COEFFICIENT
                         + df["cdd_dev"] * config.CDD_DEMAND_COEFFICIENT)
    df["week_ending"] = df.index + pd.to_timedelta(
        (3 - df.index.weekday) % 7, unit="D"
    )
    weekly_demand_bcf = df.groupby("week_ending")["demand_bcfd"].sum()
    today = pd.Timestamp(dt.date.today())
    out = {}
    for week_end, demand_bcf in weekly_demand_bcf.items():
        weeks_ahead = max(0, (week_end - today).days // 7)
        multiplier = _confidence_decay_multiplier(weeks_ahead)
        adjustment = -demand_bcf * multiplier
        out[week_end] = float(adjustment)
    return pd.Series(out, name="weather_adj_bcf").sort_index()


def _confidence_decay_multiplier(weeks_ahead: int) -> float:
    """Per spec: 100% weeks 0-2, then -15%/week, 0 at week 6+."""
    if weeks_ahead <= config.WEATHER_CONFIDENCE_DECAY_START_WEEK:
        return 1.0
    if weeks_ahead >= config.WEATHER_CONFIDENCE_DECAY_FLOOR_WEEK:
        return 0.0
    weeks_into_decay = weeks_ahead - config.WEATHER_CONFIDENCE_DECAY_START_WEEK
    return max(0.0, 1.0 - weeks_into_decay * config.WEATHER_CONFIDENCE_DECAY_PER_WEEK)


# ── Trajectory scenarios ───────────────────────────────────────────────────────


def is_injection_season(today: dt.date | None = None) -> bool:
    """True between Apr 15 and Oct 31 (injection season). Else withdrawal."""
    d = today or dt.date.today()
    return (d.month == 4 and d.day >= 15) or (4 < d.month < 11)


def season_target_date(today: dt.date | None = None) -> dt.date:
    """Nov 1 (end of injection) or Apr 1 (end of withdrawal), whichever next."""
    d = today or dt.date.today()
    if is_injection_season(d):
        return dt.date(d.year, 11, 1)
    if d.month >= 11:
        return dt.date(d.year + 1, 4, 1)
    return dt.date(d.year, 4, 1)


def build_trajectory_scenarios(
    current_storage: float,
    current_date: dt.date,
    target_date: dt.date,
    production_adj: float,
    lng_adj: float,
    demand_adj: float,
    weather_adj_series: pd.Series,
    historical_patterns: pd.DataFrame,
) -> dict[str, Any]:
    """Build base/bull/bear week-by-week trajectories.

    Returns dict with:
        dates:          list of weekly date strings (W-THU)
        base:           list of base-case storage Bcf
        bull:           list of bull-case storage Bcf (less storage, higher price)
        bear:           list of bear-case storage Bcf (more storage, lower price)
        band_low:       confidence-band lower bound (around base)
        band_high:      confidence-band upper bound (around base)
        weekly_changes: list of dicts {week, base_change, baseline, prod, lng, weather, std}
    """
    if historical_patterns.empty:
        return {"dates": [], "base": [], "bull": [], "bear": [],
                "band_low": [], "band_high": [], "weekly_changes": []}

    weeks_remaining = max(1, (target_date - current_date).days // 7)
    base_storage = bull_storage = bear_storage = current_storage
    band_low = band_high = current_storage
    out = {
        "dates": [], "base": [], "bull": [], "bear": [],
        "band_low": [], "band_high": [], "weekly_changes": [],
    }

    cur_dt = current_date
    for i in range(weeks_remaining):
        cur_dt = cur_dt + dt.timedelta(days=7)
        wk = pd.Timestamp(cur_dt).isocalendar().week
        if wk not in historical_patterns.index:
            continue
        row = historical_patterns.loc[wk]
        mean_change = float(row.get("mean_10y", row.get("mean_3y", 0)) or 0)
        bottom25_change = float(row.get("mean_bottom25_10y",
                                        row.get("mean_bottom25_3y", mean_change))
                                or mean_change)
        top25_change = float(row.get("mean_top25_10y",
                                     row.get("mean_top25_3y", mean_change))
                             or mean_change)
        std_change = float(row.get("std_10y", row.get("std_3y", 0)) or 0)

        week_ts = pd.Timestamp(cur_dt)
        weather_adj = float(weather_adj_series.get(week_ts, 0.0)) \
            if not weather_adj_series.empty else 0.0

        base_change = mean_change     + production_adj + lng_adj + demand_adj + weather_adj
        bull_change = bottom25_change + production_adj + lng_adj + demand_adj + weather_adj * 1.5
        bear_change = top25_change    + production_adj + lng_adj + demand_adj + weather_adj * 0.5

        base_storage += base_change
        bull_storage += bull_change
        bear_storage += bear_change
        weeks_ahead = (i // 4) + 1
        band = std_change * weeks_ahead
        band_low = base_storage - band
        band_high = base_storage + band

        out["dates"].append(cur_dt.isoformat())
        out["base"].append(round(base_storage, 1))
        out["bull"].append(round(bull_storage, 1))
        out["bear"].append(round(bear_storage, 1))
        out["band_low"].append(round(band_low, 1))
        out["band_high"].append(round(band_high, 1))
        out["weekly_changes"].append({
            "week": cur_dt.isoformat(),
            "base_change": round(base_change, 1),
            "baseline": round(mean_change, 1),
            "prod": round(production_adj, 1),
            "lng": round(lng_adj, 1),
            "demand": round(demand_adj, 1),
            "weather": round(weather_adj, 1),
            "std": round(std_change, 1),
        })
    return out


def calculate_end_of_season_targets(storage_df: pd.DataFrame,
                                    target_date: dt.date) -> dict[str, float | None]:
    """5y avg/max/min end-of-season storage levels for the target date.

    The "end of season" is taken to be the ISO week containing target_date.
    Returns {target_avg, target_max, target_min, min_comfortable}.
    """
    if storage_df.empty:
        return {"target_avg": None, "target_max": None, "target_min": None,
                "min_comfortable": _min_comfortable(target_date)}
    target_week = pd.Timestamp(target_date).isocalendar().week
    df = storage_df.copy()
    df["week"] = df.index.isocalendar().week
    df["year"] = df.index.year
    current_year = int(df["year"].max())
    five = df[(df["year"] < current_year) & (df["year"] >= current_year - 5)]
    same_week = five[five["week"] == target_week]["value_bcf"]
    return {
        "target_avg": float(same_week.mean()) if not same_week.empty else None,
        "target_max": float(same_week.max())  if not same_week.empty else None,
        "target_min": float(same_week.min())  if not same_week.empty else None,
        "min_comfortable": _min_comfortable(target_date),
    }


def _min_comfortable(target_date: dt.date) -> float:
    if target_date.month == 11:
        return float(config.EOS_MIN_COMFORTABLE_NOV1)
    return float(config.EOS_MIN_COMFORTABLE_APR1)


# ── Top-level orchestrator ─────────────────────────────────────────────────────


def _weather_forecast_from_weather_store(weather_data: dict | None) -> pd.DataFrame:
    """Reshape weather-data-store payload into a per-day national HDD+CDD DataFrame.

    weather_data["records"] is a list of city dicts each containing
    'daily_hdd', 'daily_cdd', 'dates', and 'weight'. Aggregate weighted HDDs
    and CDDs by date, normalized to a per-city (single-station) figure that
    aligns with the climate-normal scale.
    """
    if not weather_data or not weather_data.get("records"):
        return pd.DataFrame()
    hdd_bucket: dict[str, float] = {}
    cdd_bucket: dict[str, float] = {}
    weight_sum = 0.0
    for rec in weather_data["records"]:
        hdds = rec.get("daily_hdd") or []
        cdds = rec.get("daily_cdd") or []
        dates = rec.get("dates") or []
        weight = float(rec.get("weight", 1.0))
        weight_sum += weight
        for i, d in enumerate(dates):
            if d is None:
                continue
            if i < len(hdds) and hdds[i] is not None:
                hdd_bucket[d] = hdd_bucket.get(d, 0.0) + float(hdds[i]) * weight
            if i < len(cdds) and cdds[i] is not None:
                cdd_bucket[d] = cdd_bucket.get(d, 0.0) + float(cdds[i]) * weight
    if not hdd_bucket and not cdd_bucket:
        return pd.DataFrame()
    w = max(weight_sum, 1.0)
    all_dates = sorted(set(hdd_bucket) | set(cdd_bucket))
    df = pd.DataFrame([
        {"date": pd.to_datetime(d),
         "national_hdd": hdd_bucket.get(d, 0.0) / w,
         "national_cdd": cdd_bucket.get(d, 0.0) / w}
        for d in all_dates
    ]).set_index("date").sort_index()
    return df


def compute_trajectory(storage_data: dict | None,
                       weather_data: dict | None,
                       settings: dict | None) -> dict[str, Any]:
    """Top-level orchestrator. Returns the dcc.Store payload."""
    api_key = (settings or {}).get("eia_key") or config.EIA_API_KEY_FALLBACK
    storage_df = fetch_eia_storage_history(api_key)
    if storage_df.empty:
        return {"ready": False, "reason": "no_storage_data"}

    today = dt.date.today()
    current_storage = float(storage_df["value_bcf"].iloc[-1])
    current_date = storage_df.index[-1].date()
    target_date = season_target_date(today)
    season = "injection" if is_injection_season(today) else "withdrawal"

    prod_df = fetch_eia_production(api_key)
    lng_df = fetch_lng_exports(api_key)
    cons_df = fetch_eia_consumption(api_key)
    patterns = calculate_historical_seasonal_patterns(storage_df)

    prod_adj = calculate_production_adjustment(prod_df, storage_df)
    lng_adj = calculate_lng_adjustment(lng_df, storage_df)
    demand_adj = calculate_demand_adjustment(cons_df, storage_df)
    wx_df = _weather_forecast_from_weather_store(weather_data)
    weather_series = calculate_weather_adjustment(wx_df) if not wx_df.empty else pd.Series(dtype=float)

    scenarios = build_trajectory_scenarios(
        current_storage, current_date, target_date,
        prod_adj, lng_adj, demand_adj, weather_series, patterns,
    )
    targets = calculate_end_of_season_targets(storage_df, target_date)

    weeks_remaining = max(0, (target_date - today).days // 7)
    required_pace = None
    if targets.get("target_avg") is not None and weeks_remaining > 0:
        required_pace = (targets["target_avg"] - current_storage) / weeks_remaining

    recent_changes = storage_df["value_bcf"].diff().dropna().tail(8)
    momentum_bcfwk = None
    current_4wk_pace = None
    prior_4wk_pace = None
    if len(recent_changes) >= 8:
        current_4wk_pace = float(recent_changes.tail(4).mean())
        prior_4wk_pace = float(recent_changes.head(4).mean())
        momentum_bcfwk = current_4wk_pace - prior_4wk_pace

    def _trailing_12mo_avg(df, col):
        if df.empty: return None
        s = df[col].dropna()
        if s.empty: return None
        last = s.index[-1]
        window = s[s.index > last - pd.DateOffset(months=12)]
        return float(window.mean()) if not window.empty else None

    current_4wk_prod_avg = _trailing_12mo_avg(prod_df, "production_bcfd")
    current_4wk_lng_avg = _trailing_12mo_avg(lng_df, "lng_exports_bcfd")
    current_4wk_demand_avg = _trailing_12mo_avg(cons_df, "consumption_bcfd")

    historical_actual_ytd = _historical_actual_ytd(storage_df)
    prior_year_path = _prior_year_path(storage_df)

    return {
        "ready": True,
        "season": season,
        "current_storage": round(current_storage, 1),
        "current_date": current_date.isoformat(),
        "target_date": target_date.isoformat(),
        "weeks_remaining": weeks_remaining,
        "scenarios": scenarios,
        "targets": targets,
        "adjustments": {
            "production_bcfwk": round(prod_adj, 1),
            "lng_bcfwk": round(lng_adj, 1),
            "demand_bcfwk": round(demand_adj, 1),
            "weather_first_week_bcf": round(
                float(weather_series.iloc[0]) if not weather_series.empty else 0.0, 1
            ),
        },
        "current_4wk": {
            "production_bcfd": round(current_4wk_prod_avg, 2) if current_4wk_prod_avg else None,
            "lng_bcfd":        round(current_4wk_lng_avg, 2)  if current_4wk_lng_avg else None,
            "demand_bcfd":     round(current_4wk_demand_avg, 2) if current_4wk_demand_avg else None,
            "injection_pace":  round(current_4wk_pace, 1)     if current_4wk_pace is not None else None,
        },
        "momentum_bcfwk": round(momentum_bcfwk, 1) if momentum_bcfwk is not None else None,
        "required_pace_bcfwk": round(required_pace, 1) if required_pace is not None else None,
        "historical_actual_ytd": historical_actual_ytd,
        "prior_year_path": prior_year_path,
        "calculated_at": dt.datetime.now().isoformat(),
    }


def _historical_actual_ytd(storage_df: pd.DataFrame) -> dict[str, list]:
    """Current-year storage path so far for the trajectory chart."""
    if storage_df.empty:
        return {"dates": [], "values": []}
    cy = storage_df[storage_df.index.year == storage_df.index.year.max()]
    return {
        "dates": [d.isoformat() for d in cy.index],
        "values": [round(float(v), 1) for v in cy["value_bcf"]],
    }


def _prior_year_path(storage_df: pd.DataFrame) -> dict[str, list]:
    """Prior year full storage path for thin-dotted reference line."""
    if storage_df.empty:
        return {"weeks": [], "values": []}
    py = storage_df.index.year.max() - 1
    prior = storage_df[storage_df.index.year == py]
    if prior.empty:
        return {"weeks": [], "values": []}
    return {
        "weeks": [int(d.isocalendar().week) for d in prior.index],
        "values": [round(float(v), 1) for v in prior["value_bcf"]],
    }


# ── SQLite accuracy tracking ───────────────────────────────────────────────────


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            prediction_date  TEXT,
            target_week      TEXT,
            predicted_base   REAL,
            predicted_bull   REAL,
            predicted_bear   REAL,
            actual_storage   REAL,
            base_error_bcf   REAL,
            base_error_pct   REAL,
            PRIMARY KEY (prediction_date, target_week)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            fired_at              TEXT,
            alert_type            TEXT,
            severity              TEXT,
            message               TEXT,
            trajectory_snapshot   TEXT
        )
    """)
    conn.commit()
    return conn


def log_model_prediction(target_week: dt.date,
                         predicted_base: float,
                         predicted_bull: float,
                         predicted_bear: float,
                         actual_storage: float | None = None) -> None:
    """Insert (or upsert) a prediction record."""
    conn = _connect()
    try:
        prediction_date = dt.datetime.now().isoformat()
        base_error = base_pct = None
        if actual_storage is not None:
            base_error = predicted_base - actual_storage
            base_pct = (base_error / actual_storage) * 100 if actual_storage else None
        conn.execute(
            """
            INSERT OR REPLACE INTO predictions
                (prediction_date, target_week, predicted_base, predicted_bull,
                 predicted_bear, actual_storage, base_error_bcf, base_error_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (prediction_date, target_week.isoformat(),
             predicted_base, predicted_bull, predicted_bear,
             actual_storage, base_error, base_pct),
        )
        conn.commit()
    finally:
        conn.close()


def update_actuals_for_week(target_week: dt.date, actual_storage: float) -> int:
    """After an EIA print fills in actuals, recompute errors. Returns row count."""
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT prediction_date, predicted_base FROM predictions WHERE target_week = ?",
            (target_week.isoformat(),),
        )
        rows = cur.fetchall()
        for pred_date, predicted_base in rows:
            err = predicted_base - actual_storage
            pct = (err / actual_storage) * 100 if actual_storage else None
            conn.execute(
                """UPDATE predictions
                   SET actual_storage = ?, base_error_bcf = ?, base_error_pct = ?
                   WHERE prediction_date = ? AND target_week = ?""",
                (actual_storage, err, pct, pred_date, target_week.isoformat()),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def calculate_model_accuracy_stats() -> dict[str, Any]:
    """Aggregate MAE/RMSE/bias at 1w/2w/4w/8w horizons + 12-week rolling MAE."""
    conn = _connect()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM predictions WHERE actual_storage IS NOT NULL", conn
        )
    finally:
        conn.close()
    if df.empty:
        return {"mae_1w": None, "mae_2w": None, "mae_4w": None, "mae_8w": None,
                "rmse_1w": None, "rmse_2w": None, "rmse_4w": None, "rmse_8w": None,
                "bias": None, "rolling_12w_mae": None, "errors": []}
    df["prediction_date"] = pd.to_datetime(df["prediction_date"])
    df["target_week"] = pd.to_datetime(df["target_week"])
    df["horizon_weeks"] = ((df["target_week"] - df["prediction_date"]).dt.days // 7).clip(lower=0)
    df["abs_err"] = df["base_error_bcf"].abs()
    df["sq_err"] = df["base_error_bcf"] ** 2

    def _mae(window): return float(df[df["horizon_weeks"] == window]["abs_err"].mean()) \
        if (df["horizon_weeks"] == window).any() else None

    def _rmse(window):
        sel = df[df["horizon_weeks"] == window]["sq_err"]
        return float(np.sqrt(sel.mean())) if not sel.empty else None

    rolling = (
        df.sort_values("target_week").set_index("target_week")["abs_err"]
        .rolling("84D").mean().iloc[-1] if not df.empty else None
    )
    errors_records = (
        df.sort_values("target_week")
        [["target_week", "base_error_bcf"]]
        .assign(target_week=lambda d: d["target_week"].dt.strftime("%Y-%m-%d"))
        .to_dict("records")
    )
    return {
        "mae_1w": _mae(1), "mae_2w": _mae(2), "mae_4w": _mae(4), "mae_8w": _mae(8),
        "rmse_1w": _rmse(1), "rmse_2w": _rmse(2), "rmse_4w": _rmse(4), "rmse_8w": _rmse(8),
        "bias": float(df["base_error_bcf"].mean()),
        "rolling_12w_mae": float(rolling) if rolling is not None and not pd.isna(rolling) else None,
        "errors": errors_records,
    }


def detect_model_bias(min_samples: int = 5) -> list[dict[str, Any]]:
    """Detect calendar-period biases (consecutive ISO-week runs with same-sign error)."""
    conn = _connect()
    try:
        df = pd.read_sql_query(
            "SELECT target_week, base_error_bcf FROM predictions "
            "WHERE actual_storage IS NOT NULL", conn,
        )
    finally:
        conn.close()
    if df.empty:
        return []
    df["target_week"] = pd.to_datetime(df["target_week"])
    df["iso_week"] = df["target_week"].dt.isocalendar().week
    grouped = df.groupby("iso_week")["base_error_bcf"].agg(["mean", "count"]).reset_index()
    biases = []
    threshold_bcf = 5.0
    significant = grouped[(grouped["count"] >= min_samples)
                         & (grouped["mean"].abs() >= threshold_bcf)]
    if significant.empty:
        return []
    weeks = sorted(significant["iso_week"].tolist())
    run_start = run_end = weeks[0]
    run_signs = [significant.loc[significant["iso_week"] == run_start, "mean"].iat[0] > 0]
    runs = []
    for w in weeks[1:]:
        sign = significant.loc[significant["iso_week"] == w, "mean"].iat[0] > 0
        if w == run_end + 1 and sign == run_signs[-1]:
            run_end = w
            run_signs.append(sign)
        else:
            runs.append((run_start, run_end))
            run_start = run_end = w
            run_signs = [sign]
    runs.append((run_start, run_end))
    for start, end in runs:
        rng = significant[(significant["iso_week"] >= start)
                          & (significant["iso_week"] <= end)]
        biases.append({
            "week_start": int(start),
            "week_end": int(end),
            "avg_bias_bcf": float(rng["mean"].mean()),
            "sample_count": int(rng["count"].sum()),
        })
    return biases


def log_alert(alert_type: str, severity: str, message: str,
              trajectory_snapshot: dict | None = None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO alerts VALUES (?, ?, ?, ?, ?)",
            (dt.datetime.now().isoformat(), alert_type, severity, message,
             json.dumps(trajectory_snapshot or {})),
        )
        conn.commit()
    finally:
        conn.close()


def recent_alerts(limit: int = 20) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT fired_at, alert_type, severity, message FROM alerts "
            "ORDER BY fired_at DESC LIMIT ?", (limit,),
        )
        return [
            {"fired_at": r[0], "alert_type": r[1], "severity": r[2], "message": r[3]}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


# ── Smoke test ─────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    print("=== trajectory smoke test ===")
    storage_df = fetch_eia_storage_history(config.EIA_API_KEY_FALLBACK)
    print(f"storage history rows: {len(storage_df)}")
    if not storage_df.empty:
        print(f"  latest: {storage_df.index[-1].date()} = {storage_df['value_bcf'].iloc[-1]:,.0f} Bcf")
    prod_df = fetch_eia_production(config.EIA_API_KEY_FALLBACK)
    print(f"production rows: {len(prod_df)}")
    if not prod_df.empty:
        print(f"  latest 4-wk avg: {prod_df['production_bcfd'].tail(4).mean():.2f} Bcf/d")
    lng_df = fetch_lng_exports(config.EIA_API_KEY_FALLBACK)
    print(f"LNG rows: {len(lng_df)}")
    if not lng_df.empty:
        print(f"  latest 4-wk avg: {lng_df['lng_exports_bcfd'].tail(4).mean():.2f} Bcf/d")
    cons_df = fetch_eia_consumption(config.EIA_API_KEY_FALLBACK)
    print(f"consumption rows: {len(cons_df)}")
    if not cons_df.empty:
        print(f"  latest 4-wk avg: {cons_df['consumption_bcfd'].tail(4).mean():.2f} Bcf/d")
    patterns = calculate_historical_seasonal_patterns(storage_df)
    print(f"seasonal pattern rows: {len(patterns)}")
    prod_adj = calculate_production_adjustment(prod_df, storage_df)
    lng_adj = calculate_lng_adjustment(lng_df, storage_df)
    demand_adj = calculate_demand_adjustment(cons_df, storage_df)
    print(f"prod   adjustment: {prod_adj:+.1f} Bcf/wk")
    print(f"lng    adjustment: {lng_adj:+.1f} Bcf/wk")
    print(f"demand adjustment: {demand_adj:+.1f} Bcf/wk")
    print(f"net structural:    {prod_adj + lng_adj + demand_adj:+.1f} Bcf/wk")
    payload = compute_trajectory(None, None, None)
    print(f"compute_trajectory ready={payload.get('ready')}")
    if payload.get("ready"):
        scen = payload["scenarios"]
        print(f"  scenario weeks: {len(scen['dates'])}")
        if scen["dates"]:
            print(f"  end of season ({payload['target_date']}):")
            print(f"    base: {scen['base'][-1]:,.0f}")
            print(f"    bull: {scen['bull'][-1]:,.0f}")
            print(f"    bear: {scen['bear'][-1]:,.0f}")
        print(f"  targets: {payload['targets']}")
    log_model_prediction(dt.date.today(), 3500.0, 3300.0, 3700.0)
    stats = calculate_model_accuracy_stats()
    print(f"accuracy stats: {stats}")
    print("=== smoke test complete ===")
