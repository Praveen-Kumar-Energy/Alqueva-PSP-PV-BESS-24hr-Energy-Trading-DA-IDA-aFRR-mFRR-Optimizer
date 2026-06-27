"""
mari_mfrr_price_loader.py — mFRR capacity prices (MARI platform).

REN joined the European mFRR platform MARI on 27 Nov 2024. Live mode would query
MARI results; offline uses the mFRR ML cap-price forecaster trained on MARI proxy
data. Returns (cap_up, cap_dn) {hour: EUR/MW} + source label.

mFRR is forecast independently of aFRR — the two markets (MARI vs PICASSO) have
separate supply/demand dynamics and prices can diverge significantly.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from common_layer.configuration.config_loader import AppConfig
from common_layer.utilities.logging_utils import get_logger
from phase_3b_mfrr_manual_frequency_reserve.mfrr_price_forecasting.mfrr_price_forecaster import (
    forecast_mfrr_cap_prices,
)

log = get_logger(__name__)


def fetch_mfrr_cap_prices(hours: List[int], delivery_date: str, cfg: AppConfig,
                          use_synthetic: bool = True
                          ) -> Tuple[Dict[int, float], Dict[int, float], str]:
    if not use_synthetic:
        log.warning("Live MARI price feed not wired (out of scope) — using ML forecast")
    cap_max = cfg.market.afrr.cap_price_max_eur_mw  # mFRR shares the same REN 250 EUR/MW ceiling; no separate mFRR config field
    cap_up, cap_dn = forecast_mfrr_cap_prices(hours, delivery_date, cap_max)
    return cap_up, cap_dn, "MARI_ML_FORECAST"
