"""
pv_forecast_validator.py — PV availability forecast accuracy.

Reuses the generic error metrics; restricts MAPE to daylight hours (PV is zero at
night, so night-time relative error is undefined).
"""
from __future__ import annotations

from typing import Dict

from phase_6_backtesting_and_validation.forecast_and_model_validation.price_forecast_validator import (
    error_metrics, ErrorMetrics,
)


def validate_pv(forecast: Dict[int, float], actual: Dict[int, float]) -> ErrorMetrics:
    day_fc = {h: v for h, v in forecast.items() if v > 1e-6 or actual.get(h, 0.0) > 1e-6}
    day_ac = {h: actual.get(h, 0.0) for h in day_fc}
    return error_metrics(day_fc, day_ac)
