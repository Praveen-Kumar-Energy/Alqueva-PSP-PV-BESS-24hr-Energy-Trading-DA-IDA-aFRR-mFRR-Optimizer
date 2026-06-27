"""
omie_settlement_price_loader.py — final cleared prices for energy settlement.

Settlement values each gate's traded volume at that gate's CLEARED price (a
price-taker is settled at the marginal price, not at its bid). Live mode reads
the final OMIE marginal prices; offline we use the same deterministic forecast
the gate cleared against, so settlement is consistent with the demo run.

Returns {hour: EUR/MWh} for the requested gate.
"""
from __future__ import annotations

from typing import Dict, List

from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.da_price_forecaster import (
    forecast_da_prices,
)
from phase_2a_ida1_intraday_auction_1.ida1_price_forecasting.ida1_price_forecaster import (
    forecast_ida1_prices,
)
from phase_2b_ida2_intraday_auction_2.ida2_price_forecasting.ida2_price_forecaster import (
    forecast_ida2_prices,
)
from phase_2c_ida3_intraday_auction_3.ida3_price_forecasting.ida3_price_forecaster import (
    forecast_ida3_prices,
)


def fetch_settlement_prices(delivery_date: str, gate: str,
                            hours: List[int]) -> Dict[int, float]:
    if gate == "DA":
        return forecast_da_prices(hours, delivery_date)
    # IDA and XBID prices are modelled relative to the DA price, so every IDA
    # forecaster takes the DA prices as its base (da_prices is a required arg).
    da_prices = forecast_da_prices(hours, delivery_date)
    if gate == "IDA1":
        return forecast_ida1_prices(hours, delivery_date, da_prices)
    if gate == "IDA2":
        return forecast_ida2_prices(hours, delivery_date, da_prices)
    if gate == "IDA3":
        return forecast_ida3_prices(hours, delivery_date, da_prices)
    if gate == "XBID":
        # XBID settles at the IDA3 price (best available proxy for continuous clearing)
        return forecast_ida3_prices(hours, delivery_date, da_prices)
    raise ValueError(f"Unknown settlement gate {gate!r}")
