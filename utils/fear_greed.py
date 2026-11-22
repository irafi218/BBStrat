"""
utils/fear_greed.py
───────────────────
Fetch the Crypto Fear & Greed Index from alternative.me API.
Returns a pandas Series indexed by date string (YYYY-MM-DD).

Free API, no key required.
"""

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

API_URL   = "https://api.alternative.me/fng/"
CACHE_DIR = Path("data/historical")


def fetch_fear_greed(limit: int = 1000, use_cache: bool = True) -> pd.Series:
    """
    Fetch Fear & Greed index values.

    Returns
    -------
    pd.Series
        Index: date string 'YYYY-MM-DD'
        Values: float 0–100 (0=Extreme Fear, 100=Extreme Greed)
    """
    cache_path = CACHE_DIR / "fear_greed.csv"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ── Try cache ─────────────────────────────────────────────────────────────
    if use_cache and cache_path.exists():
        try:
            df = pd.read_csv(cache_path, index_col=0)
            # Refresh if cache is older than 1 day
            mtime = cache_path.stat().st_mtime
            age_hours = (datetime.now().timestamp() - mtime) / 3600
            if age_hours < 24:
                logger.info("Fear & Greed: loaded %d values from cache", len(df))
                return df["value"].astype(float)
        except Exception:
            pass

    # ── Fetch from API ────────────────────────────────────────────────────────
    try:
        resp = requests.get(API_URL, params={"limit": limit, "format": "json"}, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])

        records = []
        for item in data:
            ts   = int(item["timestamp"])
            date = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
            records.append({"date": date, "value": float(item["value"])})

        df = pd.DataFrame(records).set_index("date").sort_index()
        df.to_csv(cache_path)
        logger.info("Fear & Greed: fetched %d values from API", len(df))
        return df["value"].astype(float)

    except Exception as exc:
        logger.warning("Fear & Greed fetch failed: %s — using neutral (50)", exc)
        # Return empty series; strategy will treat missing data as neutral
        return pd.Series(dtype=float)


def get_fg_label(value: float) -> str:
    if value >= 75:   return "Extreme Greed"
    if value >= 55:   return "Greed"
    if value >= 45:   return "Neutral"
    if value >= 25:   return "Fear"
    return "Extreme Fear"


def get_fg_color(value: float) -> str:
    if value >= 75:   return "#f85149"   # red
    if value >= 55:   return "#e3b341"   # orange
    if value >= 45:   return "#8b949e"   # grey
    if value >= 25:   return "#58a6ff"   # blue
    return "#3fb950"                     # green
