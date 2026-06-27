"""
schema_validator.py — validate optimisation INPUTS before the MILP runs.

Phase 3A guard on the input side: bad data must be caught before it reaches the
solver, otherwise a "feasible" solve can rest on impossible inputs. Checks:
  * every delivery hour present (no gaps),
  * DA prices within OMIE technical bounds,
  * PV availability within [0, effective peak],
  * inflow non-negative,
  * initial reservoir / SOC within physical bounds.

Raises SchemaError listing every problem found (not just the first).
"""
from __future__ import annotations

from typing import Dict, List

from common_layer.configuration.config_loader import AppConfig
from common_layer.physical_plant_models.pv_production_model import PVModel


class SchemaError(ValueError):
    """Raised when input data violates the expected schema or ranges."""


def validate_inputs(data: dict, cfg: AppConfig) -> None:
    """Validate an optimisation input bundle. Raises SchemaError on any problem."""
    problems: List[str] = []

    hours: List[int] = list(data.get("hours", []))
    if not hours:
        raise SchemaError("inputs missing 'hours' (delivery hour list)")

    def _check_hourly(key: str, lo: float, hi: float, name: str) -> None:
        series: Dict[int, float] = data.get(key, {})
        for h in hours:
            if h not in series:
                problems.append(f"{name}: missing hour {h}")
                continue
            val = series[h]
            if val < lo - 1e-9 or val > hi + 1e-9:
                problems.append(f"{name}: hour {h} value {val} outside [{lo}, {hi}]")

    bl = cfg.market.bid_limits
    _check_hourly("da_prices", bl.price_min_eur_mwh, bl.price_max_eur_mwh, "da_prices")

    year = int(data.get("delivery_date", "2026-01-01")[:4])
    pv_peak = PVModel(cfg.plant.pv, year=year).effective_peak_mw
    _check_hourly("pv_available_mw", 0.0, pv_peak, "pv_available_mw")

    # Inflow: non-negative; 1e9 m3/h upper bound catches sign errors, not physical limits.
    _check_hourly("inflow_m3h", 0.0, 1e9, "inflow_m3h")

    # Initial state bounds — optional block; skipped if absent (defaults used in MILP).
    init = data.get("initial_state", {})
    if init:
        res = cfg.plant.reservoir
        u = init.get("upper_reservoir_hm3")
        if u is not None and not (res.upper_min_hm3 <= u <= res.upper_usable_hm3):
            problems.append(f"initial upper reservoir {u} outside "
                            f"[{res.upper_min_hm3}, {res.upper_usable_hm3}] hm3")
        low = init.get("lower_reservoir_hm3")
        if low is not None and not (res.lower_min_hm3 <= low <= res.lower_capacity_hm3):
            problems.append(f"initial lower reservoir {low} outside "
                            f"[{res.lower_min_hm3}, {res.lower_capacity_hm3}] hm3")
        soc = init.get("bess_soc_frac")
        b = cfg.plant.bess
        if soc is not None and not (b.soc_min_frac <= soc <= b.soc_max_frac):
            problems.append(f"initial BESS SOC {soc} outside "
                            f"[{b.soc_min_frac}, {b.soc_max_frac}]")

    if problems:
        raise SchemaError("Input validation failed:\n  - " + "\n  - ".join(problems))
