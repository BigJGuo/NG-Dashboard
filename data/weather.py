"""Open-Meteo 10-day hourly forecast fetch and HDD/CDD helpers.

Open-Meteo accepts comma-separated lat/lon lists for batch queries — we use
that to pull all 30 cities in a single HTTP round trip.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

import pandas as pd
import requests

import config


def fetch_all_cities(cities: Iterable[dict]) -> dict:
    """Return {city_name: hourly_temperature_df} for all cities in one batch call.

    Includes ``past_days=3`` so each frame contains observed temperatures for the
    last 3 days alongside the 10-day forecast — used to compute day-over-day
    temperature change vs 24h and 72h ago.
    """
    cities = list(cities)
    if not cities:
        return {}
    lats = ",".join(f"{c['lat']:.4f}" for c in cities)
    lons = ",".join(f"{c['lon']:.4f}" for c in cities)
    params = {
        "latitude":  lats,
        "longitude": lons,
        "hourly":    "temperature_2m",
        "temperature_unit": "fahrenheit",
        "forecast_days": 10,
        "past_days": 3,
        "timezone": "America/New_York",
    }
    try:
        r = requests.get(config.OPEN_METEO_URL, params=params, timeout=30)
        r.raise_for_status()
        payload = r.json()
    except Exception:
        return {}

    if isinstance(payload, dict):
        payload = [payload]

    out = {}
    for city, block in zip(cities, payload):
        hourly = block.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        if not times:
            continue
        df = pd.DataFrame({"time": pd.to_datetime(times), "temp_f": temps}).set_index("time")
        out[city["name"]] = df
    return out


def daily_summary(hourly_df: pd.DataFrame) -> pd.DataFrame:
    """Reduce hourly forecast to daily high/low/avg/HDD/CDD."""
    if hourly_df is None or hourly_df.empty:
        return pd.DataFrame()
    daily = hourly_df["temp_f"].resample("D").agg(["max", "min", "mean"])
    daily.columns = ["high_f", "low_f", "mean_f"]
    daily["hdd"] = (65 - daily["mean_f"]).clip(lower=0)
    daily["cdd"] = (daily["mean_f"] - 65).clip(lower=0)
    return daily


def climate_normal_for(month: int, region: str) -> float:
    band = config.CLIMATE_NORMALS.get(region)
    if not band:
        return 60.0
    return band[(month - 1) % 12]


def build_city_records(all_cities_data: dict, cities_meta: list[dict]) -> list[dict]:
    """Combine per-city hourly forecasts into a normalized list of records.

    Each record carries today's high/low, deviation from normal, HDD/CDD,
    the next-10-day daily series, **and** observed highs from 1 and 3 days ago
    (from Open-Meteo's ``past_days`` window) so the leaderboard can show
    real day-over-day temperature change immediately.
    """
    records = []
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    yesterday = today - pd.Timedelta(days=1)
    three_days_ago = today - pd.Timedelta(days=3)
    for city in cities_meta:
        hourly = all_cities_data.get(city["name"])
        if hourly is None or hourly.empty:
            continue
        daily = daily_summary(hourly)
        if daily.empty:
            continue

        def high_on(day):
            idx = daily.index[daily.index.normalize() == day]
            if len(idx) == 0:
                return None
            return float(daily.loc[idx[0], "high_f"])

        today_high = high_on(today)
        yesterday_high = high_on(yesterday)
        three_d_high = high_on(three_days_ago)

        today_idx = daily.index[daily.index.normalize() == today]
        today_row = daily.loc[today_idx[0]] if len(today_idx) else daily.iloc[0]
        normal = climate_normal_for(today.month, city["region"])

        # Forecast window only (today onward), excluding past_days observed history.
        forecast_window = daily.loc[daily.index.normalize() >= today].head(10)

        records.append({
            "name": city["name"],
            "lat": city["lat"], "lon": city["lon"],
            "region": city["region"], "weight": city["weight"],
            "high_f": float(today_row["high_f"]),
            "low_f":  float(today_row["low_f"]),
            "mean_f": float(today_row["mean_f"]),
            "hdd":    float(today_row["hdd"]),
            "cdd":    float(today_row["cdd"]),
            "deviation_from_normal": float(today_row["high_f"]) - normal,
            "yesterday_high":  yesterday_high,
            "three_day_high":  three_d_high,
            "change_24h": round(today_high - yesterday_high, 2) if (today_high is not None and yesterday_high is not None) else None,
            "change_72h": round(today_high - three_d_high, 2)  if (today_high is not None and three_d_high  is not None) else None,
            "daily_highs": forecast_window["high_f"].round(1).tolist(),
            "daily_hdd":   forecast_window["hdd"].round(2).tolist(),
            "daily_cdd":   forecast_window["cdd"].round(2).tolist(),
            "dates":       [d.strftime("%Y-%m-%d") for d in forecast_window.index],
        })
    return records


def forecast_revisions(current: list[dict], prior: list[dict]) -> dict:
    """Compute today's-high delta vs. a prior snapshot, keyed by city name."""
    if not prior:
        return {}
    prior_map = {r["name"]: r["high_f"] for r in prior}
    return {
        r["name"]: r["high_f"] - prior_map[r["name"]]
        for r in current if r["name"] in prior_map
    }


def national_demand_estimate(records: list[dict], days: int = 7) -> pd.DataFrame:
    """Crude national residential+commercial demand estimate.

    Sum of city HDDs (weighted) × 0.045 Bcf/d per HDD-unit.
    """
    if not records:
        return pd.DataFrame()
    dates = records[0]["dates"][:days]
    demand = []
    for d_idx in range(min(days, len(dates))):
        total_hdd = sum(r["daily_hdd"][d_idx] * r["weight"] for r in records)
        demand.append(total_hdd * 0.045)
    return pd.DataFrame({"date": pd.to_datetime(dates[:len(demand)]), "demand_bcf": demand})
