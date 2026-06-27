"""
omie_da_price_loader.py — OMIE DA price data collection + training data update.

Responsibility: keep da_training_data_2020_2026.xlsx current up to yesterday
so that da_price_forecaster.py always trains on the latest complete history.

Pipeline (called at the start of run_da.py before forecasting):

    Enter delivery_date = "2026-06-22"  (tomorrow)
            │
            ▼
    Find last date in Excel → e.g. 2025-12-31
            │
            ▼
    Gap detected: 2026-01-01 → 2026-06-21 (yesterday)
            │
            ▼
    For each missing date:
        Try OMIE download (actual published price)
            SUCCESS → append real prices to Excel
            FAIL    → append synthetic prices to Excel (pipeline never stalls)
            │
            ▼
    Excel complete up to yesterday
            │
            ▼
    da_price_forecaster.py trains on full history → predicts tomorrow

OMIE file pattern:
    https://www.omie.es/sites/default/files/dados/AGNO_<YYYY>/MES_<MM>/TXT/
    INT_PBC_EV_H_1_<DD>_<MM>_<YYYY>_<DD>_<MM>_<YYYY>.TXT

Source labels (written to Excel column 'source'):
    OMIE_LIVE   — real published OMIE price
    SYNTHETIC   — deterministic fallback (Iberian-shaped curve)
"""
from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

_EXCEL_PATH = os.path.join(os.path.dirname(__file__), "da_training_data_2020_2026.xlsx")
_SHEET      = "DA_Price_2020_2026"
_HOURS      = list(range(1, 25))

try:
    from common_layer.utilities.logging_utils import get_logger
    log = get_logger(__name__)
except Exception:
    import logging
    log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API — called by run_da.py before forecasting
# ---------------------------------------------------------------------------

def update_training_data(delivery_date: str, zone: str = "PT") -> None:
    """Fill Excel up to yesterday (day before delivery_date).

    Downloads actual OMIE prices for any missing dates between the last
    Excel entry and yesterday. Falls back to synthetic if download fails.
    Called once per pipeline run — subsequent calls skip already-filled dates.
    """
    target_dt  = pd.Timestamp(delivery_date)
    yesterday  = target_dt - pd.Timedelta(days=1)

    existing   = _load_excel()
    last_date  = existing["Date"].max() if not existing.empty else pd.Timestamp("2019-12-31")

    if last_date >= yesterday:
        log.info(f"Training data already current up to {last_date.date()} — no update needed")
        return

    # Build list of missing dates: day after last_date → yesterday
    missing = pd.date_range(start=last_date + pd.Timedelta(days=1),
                            end=yesterday, freq="D")
    log.info(f"Filling {len(missing)} missing date(s): "
             f"{missing[0].date()} → {missing[-1].date()}")

    new_rows_all = []
    for dt in missing:
        date_str = dt.strftime("%Y-%m-%d")
        try:
            prices = _download_omie_da(date_str, _HOURS, zone)
            source = "OMIE_LIVE"
            log.info(f"  {date_str} → OMIE_LIVE downloaded")
        except Exception as exc:
            prices = _synthetic_prices(date_str, _HOURS)
            source = "SYNTHETIC"
            log.warning(f"  {date_str} → OMIE failed ({exc}), using SYNTHETIC")

        for h in _HOURS:
            new_rows_all.append({
                "Date"                 : dt,
                "Hour"                 : h,
                "price_DA_PT_EUR_MWh"  : prices.get(h),
                "price_DA_ES_EUR_MWh"  : None,
                "source"               : source,
            })

    new_df  = pd.DataFrame(new_rows_all)
    updated = pd.concat([existing, new_df], ignore_index=True)
    updated = updated.sort_values(["Date", "Hour"]).reset_index(drop=True)
    _save_excel(updated)
    log.info(f"Excel updated: {len(missing)} date(s) added, "
             f"last date now {updated['Date'].max().date()}")


# ---------------------------------------------------------------------------
# Synthetic-only gap fill (used when internet unavailable / use_synthetic=True)
# ---------------------------------------------------------------------------

def _fill_synthetic_gap(delivery_date: str, zone: str = "PT") -> None:
    """Fill Excel up to yesterday using synthetic prices only (no OMIE download).

    Used in test/CI mode when OMIE is unreachable. Ensures lag features for
    da_price_forecaster are always valid regardless of network availability.
    """
    target_dt = pd.Timestamp(delivery_date)
    yesterday = target_dt - pd.Timedelta(days=1)

    existing  = _load_excel()
    last_date = existing["Date"].max() if not existing.empty else pd.Timestamp("2019-12-31")

    if last_date >= yesterday:
        return

    missing = pd.date_range(start=last_date + pd.Timedelta(days=1),
                            end=yesterday, freq="D")
    log.info(f"[Synthetic gap fill] {len(missing)} date(s): "
             f"{missing[0].date()} → {missing[-1].date()}")

    new_rows = []
    for dt in missing:
        date_str = dt.strftime("%Y-%m-%d")
        prices   = _synthetic_prices(date_str, _HOURS)
        for h in _HOURS:
            new_rows.append({
                "Date"                : dt,
                "Hour"                : h,
                "price_DA_PT_EUR_MWh" : prices.get(h),
                "price_DA_ES_EUR_MWh" : None,
                "source"              : "SYNTHETIC",
            })

    updated = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
    updated = updated.sort_values(["Date", "Hour"]).reset_index(drop=True)
    _save_excel(updated)
    log.info(f"[Synthetic gap fill] Excel updated to {updated['Date'].max().date()}")


# ---------------------------------------------------------------------------
# OMIE download
# ---------------------------------------------------------------------------

def _download_omie_da(delivery_date: str, hours: List[int],
                      zone: str) -> Dict[int, float]:
    """Download and parse OMIE marginalpdbc file. Raises on any failure."""
    import requests
    yyyy, mm, dd = delivery_date.split("-")
    url = (f"https://www.omie.es/sites/default/files/dados/"
           f"AGNO_{yyyy}/MES_{mm}/TXT/"
           f"INT_PBC_EV_H_1_{dd}_{mm}_{yyyy}_{dd}_{mm}_{yyyy}.TXT")
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()

    pt_row_idx = 1 if zone == "PT" else 0
    price_rows: List[List[str]] = []
    for line in resp.text.splitlines():
        parts = [p.strip() for p in line.split(";") if p.strip() != ""]
        if len(parts) >= 24 and all(_is_float(p) for p in parts[2:26]):
            price_rows.append(parts)
    if len(price_rows) <= pt_row_idx:
        raise ValueError("PT price row not found in OMIE file")
    row    = price_rows[pt_row_idx]
    values = [float(x) for x in row[2:26]]
    return {h: values[h - 1] for h in hours if h - 1 < len(values)}


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Synthetic fallback (deterministic Iberian-shaped curve)
# ---------------------------------------------------------------------------

def _synthetic_prices(delivery_date: str, hours: List[int],
                      mean_level: float = 55.0,
                      amplitude: float  = 22.0) -> Dict[int, float]:
    rng = random.Random(f"da-{delivery_date}")
    prices = {}
    for h in hours:
        morning   = math.exp(-((h - 9)  ** 2) / 8.0)
        evening   = math.exp(-((h - 20) ** 2) / 6.0)
        solar_dip = -0.6 * math.exp(-((h - 14) ** 2) / 10.0)
        shape     = morning + 1.15 * evening + solar_dip
        night     = -0.8 if h <= 6 or h >= 23 else 0.0
        noise     = rng.uniform(-3.0, 3.0)
        prices[h] = round(max(-500.0, mean_level + amplitude * (shape + night) + noise), 2)
    return prices


# ---------------------------------------------------------------------------
# Excel read / write
# ---------------------------------------------------------------------------

def _load_excel() -> pd.DataFrame:
    df = pd.read_excel(_EXCEL_PATH, sheet_name=_SHEET)
    df.columns    = [c.strip() for c in df.columns]
    df["Date"]    = pd.to_datetime(df["Date"])
    return df


def _save_excel(df: pd.DataFrame) -> None:
    with pd.ExcelWriter(_EXCEL_PATH, engine="openpyxl", mode="w") as writer:
        df.to_excel(writer, sheet_name=_SHEET, index=False)
