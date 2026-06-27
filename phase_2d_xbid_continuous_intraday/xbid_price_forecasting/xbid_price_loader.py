"""
xbid_price_loader.py — XBID continuous intraday prices.

No live order-book feed (commercial EPEX SPOT subscription required). Offline proxy:
ML spread model trained on synthetic XBID mid-price data (IDA3 + OU noise, std ~14
EUR/MWh). A small per-window drift is added on top to approximate the intra-window
price movement seen in continuous markets.

XBID closes 1 hour before each delivery period; tradable_hours_for_window() returns
the still-open hours for each check window.
"""
from __future__ import annotations

import random
from typing import Dict, List

from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.da_price_forecaster import (
    forecast_da_prices,
)
from phase_2d_xbid_continuous_intraday.xbid_price_forecasting.xbid_price_forecaster import (
    forecast_xbid_prices,
)


def fetch_xbid_prices(hours: List[int], delivery_date: str, window: str) -> Dict[int, float]:
    """XBID proxy prices for a check window ('W1' = D-1 18:30, 'W2' = D 09:30).

    Uses the XBID ML spread model as the base, then adds a small per-window drift
    (±1.5 EUR/MWh) to approximate order-book movement between windows.
    """
    da_prices  = forecast_da_prices(hours, delivery_date)
    base       = forecast_xbid_prices(hours, delivery_date, da_prices)
    rng        = random.Random(f"xbid-{window}-{delivery_date}")
    return {h: round(max(1.0, base[h] + rng.uniform(-1.5, 1.5)), 2) for h in hours}


def tradable_hours_for_window(all_hours: List[int], window: str) -> List[int]:
    """Hours still open at the window (XBID closes 1h before delivery).

    W1 (D-1 18:30): the whole delivery day is >1h away -> all hours open.
    W2 (D 09:30):  hours up to ~10:30 are within 1h or past -> hours >= 11 open.
    """
    if window == "W1":
        return list(all_hours)
    return [h for h in all_hours if h >= 11]
