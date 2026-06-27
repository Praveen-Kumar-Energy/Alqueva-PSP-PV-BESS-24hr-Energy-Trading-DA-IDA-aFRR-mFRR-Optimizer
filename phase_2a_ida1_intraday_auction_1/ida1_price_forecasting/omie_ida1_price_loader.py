"""
omie_ida1_price_loader.py — IDA1 intraday auction price (D-1 15:00 CET close).

IDA1 clears the whole delivery day (all 24 hours tradable) with fresher
information than DA. Live mode would pull the published SIDC/OMIE IDA1 result;
offline we use the shared ML intraday forecaster with IDA1 lead-time (24h).

Returned shape: {hour: EUR/MWh} for hours 1..24.
"""
from __future__ import annotations

from typing import Dict, List

from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.da_price_forecaster import (
    forecast_da_prices,
)
from phase_2a_ida1_intraday_auction_1.ida1_price_forecasting.ida1_price_forecaster import (
    forecast_ida1_prices,
)


def fetch_ida1_prices(hours: List[int], delivery_date: str) -> Dict[int, float]:
    """Return {hour: EUR/MWh} IDA1 clearing price forecast.

    DA prices computed first (already cached if run_da ran earlier in session),
    then the IDA1-specific ML spread model adds the intraday deviation
    (gate closes D-1 15:00 CET, covers H1-H24).
    """
    da_prices = forecast_da_prices(hours, delivery_date)
    return forecast_ida1_prices(hours, delivery_date, da_prices)
