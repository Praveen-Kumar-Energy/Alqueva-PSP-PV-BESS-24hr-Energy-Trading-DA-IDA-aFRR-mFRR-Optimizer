"""
psp_turbine_pump_model.py — physics + validation for the 4 reversible units.

Reversible Francis pump-turbines: each unit can GENERATE (turbine), PUMP, or be
OFF — the three modes are mutually exclusive in any period.

This module is pure Python (no solver). It supplies:
  * physical limits (per unit and plant total),
  * a flow model (power <-> water flow),
  * validation of a dispatch against the spec's prohibitions/invariants.

Spec mapping enforced by `validate_unit`:
  PR-1  no pump AND turbine in the same period (mode exclusivity)
  PR-2  turbine is OFF or >= min stable load, never strictly between 0 and 57 MW
  PR-3  pump intake never exceeds per-unit pump max
  PR-4  (plant level, in validate_plant) totals within generation/pump envelope
  INV-5 mode flags binary, sum <= 1 per unit

SIGN CONVENTION: turbine_mw and pump_mw are stored as non-negative MAGNITUDES.
Net power = turbine_mw - pump_mw  (generation positive, pumping negative).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from common_layer.configuration.plant_config import PSPConfig

# Numerical tolerance for floating-point comparisons (MW / m3h).
EPS_MW = 1e-6


@dataclass
class UnitDispatch:
    """One unit's dispatch in one period. Magnitudes, both >= 0."""
    turbine_mw: float = 0.0
    pump_mw: float = 0.0

    @property
    def mode(self) -> str:
        if self.turbine_mw > EPS_MW:
            return "turbine"
        if self.pump_mw > EPS_MW:
            return "pump"
        return "off"

    @property
    def net_mw(self) -> float:
        """Net power: + generation, - pumping."""
        return self.turbine_mw - self.pump_mw


class PSPModel:
    """Physics and validation for the pump-turbine fleet."""

    def __init__(self, cfg: PSPConfig):
        self.cfg = cfg

    # -- limits -------------------------------------------------------------
    @property
    def n_units(self) -> int:
        return self.cfg.n_units

    @property
    def turbine_max_mw(self) -> float:
        return self.cfg.p_turbine_max_mw

    @property
    def turbine_min_mw(self) -> float:
        return self.cfg.p_turbine_min_mw

    @property
    def pump_max_mw(self) -> float:
        return self.cfg.p_pump_max_mw

    # -- flow model ---------------------------------------------------------
    def turbine_flow_m3h(self, turbine_mw: float) -> float:
        """Water discharged for a given turbine power (m3/h).

        Linear interpolation between (p_min -> q_min) and (p_max -> q_max).
        Off => zero flow. This is a documented approximation of the true
        head-dependent efficiency surface; refined in the MILP if needed."""
        if turbine_mw <= EPS_MW:
            return 0.0
        p_lo, p_hi = self.cfg.p_turbine_min_mw, self.cfg.p_turbine_max_mw
        q_lo, q_hi = self.cfg.q_turbine_min_m3h, self.cfg.q_turbine_max_m3h
        frac = (turbine_mw - p_lo) / (p_hi - p_lo) if p_hi > p_lo else 0.0
        return q_lo + frac * (q_hi - q_lo)

    def pump_flow_m3h(self, pump_mw: float) -> float:
        """Water lifted for a given pump power (m3/h), linear approximation.

        Interpolates between (p_pump_min_derived, q_min) and (p_max, q_max) where
        p_pump_min_derived = p_pump_max × (q_min / q_max). This base point matches
        the MILP formulation so that the checker and solver use identical flow curves."""
        if pump_mw <= EPS_MW:
            return 0.0
        # Derived minimum pump power — same formula as core_milp_builder.
        p_lo = self.cfg.p_pump_max_mw * (self.cfg.q_pump_min_m3h / self.cfg.q_pump_max_m3h)
        p_hi = self.cfg.p_pump_max_mw
        q_lo, q_hi = self.cfg.q_pump_min_m3h, self.cfg.q_pump_max_m3h
        frac = (pump_mw - p_lo) / (p_hi - p_lo) if p_hi > p_lo else 0.0
        return q_lo + frac * (q_hi - q_lo)

    # -- validation ---------------------------------------------------------
    def validate_unit(self, d: UnitDispatch, label: str = "") -> List[str]:
        """Return a list of human-readable violations (empty == feasible)."""
        v: List[str] = []
        tag = f"[{label}] " if label else ""

        # PR-1 / INV-5: mode exclusivity — never pump and turbine together.
        if d.turbine_mw > EPS_MW and d.pump_mw > EPS_MW:
            v.append(f"{tag}PR-1 mode conflict: turbine={d.turbine_mw:.3f} MW AND "
                     f"pump={d.pump_mw:.3f} MW in same period")

        # PR-2: turbine OFF or >= min stable load, never strictly in (0, min).
        if EPS_MW < d.turbine_mw < self.cfg.p_turbine_min_mw - EPS_MW:
            v.append(f"{tag}PR-2 turbine below min stable load: {d.turbine_mw:.3f} MW "
                     f"< {self.cfg.p_turbine_min_mw:.1f} MW (must be 0 or >= min)")
        if d.turbine_mw > self.cfg.p_turbine_max_mw + EPS_MW:
            v.append(f"{tag}PR-2 turbine over max: {d.turbine_mw:.3f} MW "
                     f"> {self.cfg.p_turbine_max_mw:.1f} MW")

        # PR-3: pump intake within rating, and (informational) above min if running.
        if d.pump_mw > self.cfg.p_pump_max_mw + EPS_MW:
            v.append(f"{tag}PR-3 pump over max: {d.pump_mw:.3f} MW "
                     f"> {self.cfg.p_pump_max_mw:.1f} MW")
        if d.turbine_mw < -EPS_MW or d.pump_mw < -EPS_MW:
            v.append(f"{tag}negative magnitude: turbine={d.turbine_mw}, pump={d.pump_mw}")
        return v

    def validate_plant(self, units: List[UnitDispatch], label: str = "") -> List[str]:
        """Validate the whole fleet for one period: per-unit + PR-4 totals."""
        v: List[str] = []
        for i, d in enumerate(units, start=1):
            v.extend(self.validate_unit(d, label=f"{label} unit{i}".strip()))

        total_turbine = sum(d.turbine_mw for d in units)
        total_pump = sum(d.pump_mw for d in units)
        if total_turbine > self.cfg.total_turbine_max_mw + EPS_MW:
            v.append(f"[{label}] PR-4 fleet turbine {total_turbine:.2f} MW "
                     f"> {self.cfg.total_turbine_max_mw:.1f} MW")
        if total_pump > self.cfg.total_pump_max_mw + EPS_MW:
            v.append(f"[{label}] PR-4 fleet pump {total_pump:.2f} MW "
                     f"> {self.cfg.total_pump_max_mw:.1f} MW")
        return v

    def net_power_mw(self, units: List[UnitDispatch]) -> float:
        """Fleet net power: + generation, - pumping."""
        return sum(d.net_mw for d in units)
