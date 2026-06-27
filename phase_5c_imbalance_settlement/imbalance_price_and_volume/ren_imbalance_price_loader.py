"""
ren_imbalance_price_loader.py — REN dual imbalance prices.

Dual pricing penalises being out of balance: a SHORT position (under-delivery) is
bought back at a premium to the energy price; a LONG position (over-delivery) is
sold at a discount. Live mode reads REN DataHub ISP prices; offline we apply the
configured fallback multipliers to the DA price.

Returns (short_price, long_price) each {hour: EUR/MWh}.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from common_layer.configuration.config_loader import AppConfig
from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.da_price_forecaster import (
    forecast_da_prices,
)


def fetch_imbalance_prices(delivery_date: str, hours: List[int], cfg: AppConfig
                           ) -> Tuple[Dict[int, float], Dict[int, float]]:
    da = forecast_da_prices(hours, delivery_date)
    imb = cfg.market.imbalance
    short = {h: round(da[h] * imb.fallback_short_factor, 2) for h in hours}
    long_ = {h: round(da[h] * imb.fallback_long_factor, 2) for h in hours}
    return short, long_
