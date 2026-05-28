"""EIA natural gas weekly storage fetch + seasonal band computation."""
from __future__ import annotations

import datetime as dt
import pandas as pd
import requests

import config


# EIA v2 facet codes for each storage region we surface. Confirmed empirically
# against https://api.eia.gov/v2/natural-gas/stor/wkly/ — the v2 endpoint orders
# regions alphabetically (East, Midwest, Mountain, Pacific, South Central),
# which does NOT match the legacy v1 R-code numbering (where R40 was Pacific).
# Salt / Non-Salt are sub-processes of South Central (R33), not separate
# duoarea codes.
REGION_FACETS = {
    "East":                   ("R31", "SWO"),
    "Midwest":                ("R32", "SWO"),
    "South Central":          ("R33", "SWO"),
    "Mountain":               ("R34", "SWO"),
    "Pacific":                ("R35", "SWO"),
    "South Central Salt":     ("R33", "SSO"),
    "South Central Non-Salt": ("R33", "SNO"),
}
_FACETS_TO_REGION = {v: k for k, v in REGION_FACETS.items()}


def fetch_storage_weekly(api_key: str) -> pd.DataFrame:
    """Pull weekly working gas in underground storage (lower 48) from EIA v2 API.

    Returns DataFrame indexed by period (date) with columns ['value_bcf'].
    Returns empty DataFrame on any failure.
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
        "length": 500,
    }
    try:
        r = requests.get(config.EIA_STORAGE_URL, params=params, timeout=20)
        r.raise_for_status()
        payload = r.json()
        rows = payload.get("response", {}).get("data", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["period"] = pd.to_datetime(df["period"])
        df["value_bcf"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value_bcf"]).sort_values("period")
        return df[["period", "value_bcf"]].set_index("period")
    except Exception:
        return pd.DataFrame()


def compute_seasonal_bands(df: pd.DataFrame) -> pd.DataFrame:
    """Compute 5-year min/max/avg by week-of-year using all but the current year."""
    if df.empty:
        return pd.DataFrame()
    s = df["value_bcf"].copy()
    week = s.index.isocalendar().week
    year = s.index.year
    current_year = year.max()
    prior5 = s[(year < current_year) & (year >= current_year - 5)]
    if prior5.empty:
        return pd.DataFrame()
    grouped = prior5.groupby(prior5.index.isocalendar().week)
    bands = pd.DataFrame({
        "min_5y": grouped.min(),
        "max_5y": grouped.max(),
        "avg_5y": grouped.mean(),
    })
    bands.index.name = "week"
    return bands


def weekly_changes(df: pd.DataFrame, n: int = 52) -> pd.Series:
    """Return last ``n`` weekly net changes (positive = injection, negative = draw)."""
    if df.empty:
        return pd.Series(dtype=float)
    return df["value_bcf"].diff().dropna().tail(n)


def next_eia_release(now: dt.datetime) -> dt.datetime:
    """Next Thursday 10:30am ET after ``now`` (timezone-aware)."""
    weekday = now.weekday()  # Mon=0
    days_ahead = (3 - weekday) % 7
    target = now.replace(hour=10, minute=30, second=0, microsecond=0) + dt.timedelta(days=days_ahead)
    if target <= now:
        target += dt.timedelta(days=7)
    return target


def storage_year_start(today: dt.date) -> dt.date:
    """Storage year begins April 1. Return the start of the current storage year."""
    if today.month >= 4:
        return dt.date(today.year, 4, 1)
    return dt.date(today.year - 1, 4, 1)


def cumulative_paths(df: pd.DataFrame, bands: pd.DataFrame) -> pd.DataFrame:
    """Actual vs 5y-typical cumulative net injection since April 1 of the storage year.

    Returns a DataFrame indexed by period with columns:
      - ``actual``  — cumulative net injection (Bcf) since storage-year start
      - ``avg_5y``  — what cumulative net injection would have been at the same
                     week-of-year on the 5y average path
      - ``surplus`` — ``actual - avg_5y`` (positive = looser than norm)

    By construction all three start at 0 on the first observation on/after April 1.
    """
    if df.empty or bands.empty:
        return pd.DataFrame(columns=["actual", "avg_5y", "surplus"])
    start = pd.Timestamp(storage_year_start(dt.date.today()))
    cy = df[df.index >= start].copy()
    if cy.empty:
        return pd.DataFrame(columns=["actual", "avg_5y", "surplus"])
    cy["week"] = cy.index.isocalendar().week
    cy = cy.join(bands["avg_5y"], on="week")
    cy = cy.dropna(subset=["avg_5y"])
    if cy.empty:
        return pd.DataFrame(columns=["actual", "avg_5y", "surplus"])
    anchor_level = cy["value_bcf"].iloc[0]
    anchor_avg5y = cy["avg_5y"].iloc[0]
    out = pd.DataFrame(index=cy.index)
    out["actual"]  = cy["value_bcf"] - anchor_level
    out["avg_5y"] = cy["avg_5y"]   - anchor_avg5y
    out["surplus"] = out["actual"] - out["avg_5y"]
    return out


def cumulative_surplus(df: pd.DataFrame, bands: pd.DataFrame) -> pd.Series:
    """Back-compat wrapper: returns just the surplus column from cumulative_paths()."""
    paths = cumulative_paths(df, bands)
    if paths.empty:
        return pd.Series(dtype=float)
    return paths["surplus"]


def _fetch_storage_facets(api_key: str, duoarea_codes: list[str],
                          process_codes: list[str]) -> pd.DataFrame:
    """One batched EIA call for the cartesian product of duoarea × process.

    Returns a DataFrame with columns ['period', 'value_bcf', 'duoarea',
    'process'] (empty on failure).
    """
    params: list[tuple] = [
        ("api_key", api_key),
        ("frequency", "weekly"),
        ("data[0]", "value"),
        ("start", "2010-01-01"),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "desc"),
        ("offset", 0),
        ("length", 5000),
    ]
    for c in duoarea_codes:
        params.append(("facets[duoarea][]", c))
    for p in process_codes:
        params.append(("facets[process][]", p))
    try:
        r = requests.get(config.EIA_STORAGE_URL, params=params, timeout=30)
        r.raise_for_status()
        rows = r.json().get("response", {}).get("data", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["period"] = pd.to_datetime(df["period"])
        df["value_bcf"] = pd.to_numeric(df["value"], errors="coerce")
        return df.dropna(subset=["value_bcf"])[
            ["period", "value_bcf", "duoarea", "process"]
        ]
    except Exception:
        return pd.DataFrame()


def fetch_regional_storage(api_key: str) -> dict:
    """Batch-fetch weekly working gas for the five EIA regions plus the two
    South Central sub-regions (Salt, Non-Salt).

    Returns dict keyed by display name (East, Midwest, Mountain, Pacific,
    South Central, South Central Salt, South Central Non-Salt). Each value is
    a DataFrame indexed by period with column ``value_bcf``. Empty dict on
    failure.

    Uses two batched calls (EIA v2 caps each at 5000 rows): one for the five
    SWO-process region totals, one for the two SSO/SNO sub-processes scoped to
    duoarea R34. With ~850 weekly observations per series since 2010 each call
    stays comfortably under the cap.
    """
    if not api_key:
        return {}
    # Call 1: five regional totals, SWO process.
    regional_codes = sorted({d for (d, p) in REGION_FACETS.values() if p == "SWO"})
    df_regions = _fetch_storage_facets(api_key, regional_codes, ["SWO"])
    # Call 2: South Central (R33) Salt + Non-Salt sub-processes.
    sc_duoarea = REGION_FACETS["South Central"][0]
    df_subs = _fetch_storage_facets(api_key, [sc_duoarea], ["SSO", "SNO"])
    df = pd.concat([df_regions, df_subs], ignore_index=True)
    if df.empty:
        return {}
    out: dict[str, pd.DataFrame] = {}
    for (duoarea, process), region_name in _FACETS_TO_REGION.items():
        sub = df[(df["duoarea"] == duoarea) & (df["process"] == process)][
            ["period", "value_bcf"]
        ]
        if sub.empty:
            continue
        sub = sub.sort_values("period").set_index("period")
        # Drop duplicate periods if EIA returned overlapping rows.
        sub = sub[~sub.index.duplicated(keep="last")]
        out[region_name] = sub
    return out


def calculate_regional_stats(region_df: pd.DataFrame) -> dict | None:
    """Compute current-week stats for a single region's storage history.

    Returns dict with current_bcf, avg_5y_bcf, dev_bcf, dev_pct, yoy_bcf,
    wow_bcf, label (surplus|deficit), last_date (ISO), sparkline_8w (list).
    Returns None on empty input.
    """
    if region_df is None or region_df.empty:
        return None
    s = region_df["value_bcf"]
    last_idx = s.index[-1]
    current = float(s.iloc[-1])
    week = int(last_idx.isocalendar().week)

    bands = compute_seasonal_bands(region_df)
    avg_5y = (float(bands.loc[week, "avg_5y"])
              if (not bands.empty and week in bands.index) else None)

    dev_bcf = (current - avg_5y) if avg_5y is not None else None
    dev_pct = ((dev_bcf / avg_5y) * 100.0) if (dev_bcf is not None and avg_5y) else None

    wow_bcf = float(s.iloc[-1] - s.iloc[-2]) if len(s) >= 2 else None

    yoy_target = last_idx - pd.Timedelta(weeks=52)
    yoy_window = s[(s.index >= yoy_target - pd.Timedelta(days=4)) &
                   (s.index <= yoy_target + pd.Timedelta(days=4))]
    yoy_bcf = float(current - yoy_window.iloc[0]) if not yoy_window.empty else None

    label = "surplus" if (dev_bcf is not None and dev_bcf >= 0) else "deficit"

    return {
        "current_bcf": current,
        "avg_5y_bcf":  avg_5y,
        "dev_bcf":     dev_bcf,
        "dev_pct":     dev_pct,
        "yoy_bcf":     yoy_bcf,
        "wow_bcf":     wow_bcf,
        "label":       label,
        "last_date":   last_idx.isoformat(),
        "sparkline_8w": [float(v) for v in s.tail(8).tolist()],
    }
