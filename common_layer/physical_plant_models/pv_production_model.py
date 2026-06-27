"""
pv_production_model.py — floating PV production physics + validation.

Converts plane-of-array irradiance and cell temperature into AC power for the
5 MWp Alqueva floating array, applying the standard temperature derate and a
linear annual degradation since commissioning (2022).

Spec mapping:
  PR-10  scheduled/used PV never exceeds available PV for that hour, and PV
         output never exceeds the (degraded) peak capacity.

Standard PV power model:
    P = P_peak * (G / G_ref) * [1 + gamma * (T_cell - T_ref)] * (1 - deg)^years
where gamma is the temperature coefficient (negative), G_ref = 1000 W/m2,
T_ref = 25 C. Result clamped to [0, degraded peak].
"""
from __future__ import annotations

from typing import List

from common_layer.configuration.plant_config import PVConfig

EPS_MW = 1e-6


class PVModel:
    def __init__(self, cfg: PVConfig, year: int):
        self.cfg = cfg
        self.year = year

    @property
    def degradation_factor(self) -> float:
        """(1 - deg_rate) compounded over years since commissioning."""
        years = max(0, self.year - self.cfg.commission_year)
        return (1.0 - self.cfg.degradation_rate_per_year) ** years

    @property
    def effective_peak_mw(self) -> float:
        """Peak capacity after degradation — the hard ceiling for PR-10."""
        return self.cfg.peak_capacity_mw * self.degradation_factor

    def production_mw(self, irradiance_wm2: float, cell_temp_c: float) -> float:
        """Physical PV AC power for one hour, clamped to [0, effective peak]."""
        if irradiance_wm2 <= 0.0:
            return 0.0
        temp_factor = 1.0 + self.cfg.temperature_coeff_per_c * (cell_temp_c - self.cfg.t_ref_c)
        raw = (self.cfg.peak_capacity_mw
               * (irradiance_wm2 / self.cfg.g_ref_wm2)
               * temp_factor
               * self.degradation_factor)
        return min(max(raw, 0.0), self.effective_peak_mw)

    # -- validation ---------------------------------------------------------
    def validate_used_pv(self, used_mw: float, available_mw: float,
                         label: str = "") -> List[str]:
        """PR-10: PV used in a schedule must not exceed available PV, nor peak."""
        v: List[str] = []
        tag = f"[{label}] " if label else ""
        if used_mw > available_mw + EPS_MW:
            v.append(f"{tag}PR-10 PV used {used_mw:.3f} > available {available_mw:.3f} MW")
        if used_mw > self.effective_peak_mw + EPS_MW:
            v.append(f"{tag}PR-10 PV used {used_mw:.3f} > peak {self.effective_peak_mw:.3f} MW")
        if used_mw < -EPS_MW:
            v.append(f"{tag}PR-10 PV used negative: {used_mw:.3f} MW")
        return v
