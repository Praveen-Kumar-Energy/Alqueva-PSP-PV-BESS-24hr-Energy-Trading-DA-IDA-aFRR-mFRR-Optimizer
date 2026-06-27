"""
historical_data_loader.py — dates and 'actual' realisations for backtesting.

Offline we replay a span of delivery days. For forecast validation we also need
the REALISED series to compare against the forecast; we synthesise it as the
forecast plus a perturbation (the forecast error). Live mode would load archived
OMIE/REN history instead.
"""
from __future__ import annotations

import datetime as dt
import random
from typing import Dict, List


def date_range(start_date: str, n_days: int) -> List[str]:
    start = dt.datetime.strptime(start_date, "%Y-%m-%d").date()
    return [(start + dt.timedelta(days=i)).isoformat() for i in range(n_days)]


def realised_from_forecast(forecast: Dict[int, float], delivery_date: str,
                           rel_error: float = 0.10, tag: str = "px") -> Dict[int, float]:
    """Synthetic 'actual' = forecast * (1 +/- error). Deterministic per date."""
    rng = random.Random(f"{tag}-actual-{delivery_date}")
    return {h: round(v * (1.0 + rng.uniform(-rel_error, rel_error)), 4)
            for h, v in forecast.items()}
