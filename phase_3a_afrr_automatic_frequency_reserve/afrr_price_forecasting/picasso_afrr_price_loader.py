"""
picasso_afrr_price_loader.py — aFRR capacity prices (national platform / PICASSO).

As of 2026 Portugal runs aFRR on a national platform; REN's PICASSO accession is
expected ~Q3 2026 (config afrr.platform). Live mode would query the relevant
platform result; offline uses the ML cap-price forecaster trained on REN/eSIO data.
Prices bounded by the REN cap-price ceiling (config afrr.cap_price_max_eur_mw = 250 EUR/MW).

Returns (cap_up, cap_dn) each {hour: EUR/MW} plus the platform label.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from common_layer.configuration.config_loader import AppConfig
from common_layer.utilities.logging_utils import get_logger
from phase_3a_afrr_automatic_frequency_reserve.afrr_price_forecasting.afrr_price_forecaster import (
    forecast_afrr_cap_prices,
)

log = get_logger(__name__)


def fetch_afrr_cap_prices(hours: List[int], delivery_date: str, cfg: AppConfig,
                          use_synthetic: bool = True
                          ) -> Tuple[Dict[int, float], Dict[int, float], str]:
    platform = cfg.market.afrr.platform
    cap_max  = cfg.market.afrr.cap_price_max_eur_mw
    if not use_synthetic:
        log.warning("Live aFRR price feed not wired (out of scope) — using ML forecast")
    cap_up, cap_dn = forecast_afrr_cap_prices(hours, delivery_date, cap_max)
    return cap_up, cap_dn, f"{platform}_ML_FORECAST"
