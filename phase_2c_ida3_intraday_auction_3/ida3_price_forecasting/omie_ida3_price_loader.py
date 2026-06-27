"""
omie_ida3_price_loader.py — IDA3 intraday auction price (gate closes D 10:00 CET).

IDA3 is the last SIDC intraday auction. Tradable hours H12-H24 only;
H1-H11 are frozen (committed in IDA1/IDA2, past the IDA3 gate window).
Live mode would pull the published SIDC/OMIE IDA3 result; offline we use the
IDA3-specific ML spread model trained on SIDC IDA3 clearing prices.

Training data and model json specific to Phase 2c:
    ida3_training_data_2024_2025.xlsx  ->  phase_2c/price_and_power_forecasting/
    ida3_selected_model.json           ->  phase_2c/price_and_power_forecasting/

Returned shape: {hour: EUR/MWh} for hours 12..24.
"""
from __future__ import annotations

from typing import Dict, List

from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.da_price_forecaster import (
    forecast_da_prices,
)
from phase_2c_ida3_intraday_auction_3.ida3_price_forecasting.ida3_price_forecaster import (
    forecast_ida3_prices,
)


def fetch_ida3_prices(hours: List[int], delivery_date: str) -> Dict[int, float]:
    """Return {hour: EUR/MWh} IDA3 clearing price forecast for tradable hours.

    DA prices computed first (cached if run_ida3 called after DA/IDA1/IDA2 in session),
    then the IDA3-specific ML spread model adds the intraday deviation
    (gate closes D 10:00 CET, covers H13-H24).
    """
    da_prices = forecast_da_prices(hours, delivery_date)
    return forecast_ida3_prices(hours, delivery_date, da_prices)
