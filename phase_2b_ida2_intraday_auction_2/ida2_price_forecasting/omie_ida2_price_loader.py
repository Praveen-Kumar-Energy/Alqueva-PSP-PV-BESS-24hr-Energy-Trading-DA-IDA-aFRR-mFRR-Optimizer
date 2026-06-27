"""
omie_ida2_price_loader.py — IDA2 intraday auction price (D-1 22:00 CET close).

IDA2 clears at D-1 22:00 CET with fresher information than IDA1.
Tradable hours: h3-h24 (h1-h2 already frozen after IDA1).
Live mode would pull the published SIDC/OMIE IDA2 result; offline we use the
IDA2-specific ML spread model trained on SIDC IDA2 clearing prices.

Training data and model json specific to Phase 2b:
    ida2_training_data_2024_2025.xlsx  ->  phase_2b/price_and_power_forecasting/
    ida2_selected_model.json           ->  phase_2b/price_and_power_forecasting/

Returned shape: {hour: EUR/MWh} for hours 3..24.
"""
from __future__ import annotations

from typing import Dict, List

from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.da_price_forecaster import (
    forecast_da_prices,
)
from phase_2b_ida2_intraday_auction_2.ida2_price_forecasting.ida2_price_forecaster import (
    forecast_ida2_prices,
)


def fetch_ida2_prices(hours: List[int], delivery_date: str) -> Dict[int, float]:
    """Return {hour: EUR/MWh} IDA2 clearing price forecast for tradable hours.

    DA prices computed first (cached if run_ida2 called after DA/IDA1 in session),
    then the IDA2 ML spread model adds the gate-specific deviation
    (gate closes D-1 22:00 CET).
    """
    da_prices = forecast_da_prices(hours, delivery_date)
    return forecast_ida2_prices(hours, delivery_date, da_prices)
