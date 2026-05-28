"""CFTC Disaggregated COT report fetch + parse for NYMEX Henry Hub natural gas.

The Henry Hub natural gas futures contract is reported as
``NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE`` in the **Disaggregated**
report (``com_disagg_xls_{year}.zip``). The legacy ``deacot{year}.zip``
file only contains the ICE San Juan index and lacks the Managed Money /
Swap Dealer / Producer breakdown we need.
"""
from __future__ import annotations

import datetime as dt
import io
import zipfile

import pandas as pd
import requests

CFTC_DISAGG_URL = "https://www.cftc.gov/files/dea/history/com_disagg_xls_{year}.zip"
NG_MARKET_NAME = "NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE"


def fetch_cot_year(year: int) -> pd.DataFrame:
    """Download one year of Disaggregated COT and return NYMEX Henry Hub NG rows."""
    url = CFTC_DISAGG_URL.format(year=year)
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
    except Exception:
        return pd.DataFrame()
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
            if not names:
                return pd.DataFrame()
            with zf.open(names[0]) as fh:
                df = pd.read_excel(fh)
    except Exception:
        return pd.DataFrame()

    if "Market_and_Exchange_Names" not in df.columns:
        return pd.DataFrame()
    ng = df[df["Market_and_Exchange_Names"] == NG_MARKET_NAME].copy()
    if ng.empty:
        return pd.DataFrame()
    return _normalize(ng)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Pull the seven trader-category fields plus report date into a clean frame."""
    date_col = "Report_Date_as_MM_DD_YYYY" if "Report_Date_as_MM_DD_YYYY" in df.columns \
        else next((c for c in df.columns if "report_date" in c.lower()), None)
    if not date_col:
        return pd.DataFrame()

    def col(*candidates):
        for c in candidates:
            if c in df.columns:
                return df[c]
        return pd.Series([0] * len(df))

    out = pd.DataFrame({
        "report_date": pd.to_datetime(df[date_col], errors="coerce"),
        "mm_long":     pd.to_numeric(col("M_Money_Positions_Long_ALL"),  errors="coerce"),
        "mm_short":    pd.to_numeric(col("M_Money_Positions_Short_ALL"), errors="coerce"),
        "prod_long":   pd.to_numeric(col("Prod_Merc_Positions_Long_ALL"),  errors="coerce"),
        "prod_short":  pd.to_numeric(col("Prod_Merc_Positions_Short_ALL"), errors="coerce"),
        "swap_long":   pd.to_numeric(col("Swap_Positions_Long_All"),  errors="coerce"),
        "swap_short":  pd.to_numeric(col("Swap__Positions_Short_All", "Swap_Positions_Short_All"), errors="coerce"),
    }).dropna(subset=["report_date"]).sort_values("report_date")

    out["mm_net"]   = out["mm_long"]   - out["mm_short"]
    out["prod_net"] = out["prod_long"] - out["prod_short"]
    out["swap_net"] = out["swap_long"] - out["swap_short"]
    return out.set_index("report_date")


def build_3yr_history() -> pd.DataFrame:
    today = dt.date.today()
    frames = []
    for year in (today.year - 2, today.year - 1, today.year):
        df = fetch_cot_year(year)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames).sort_index()


def percentile_rank(series: pd.Series, value: float) -> float:
    if series.empty:
        return 50.0
    return float((series <= value).mean() * 100)
