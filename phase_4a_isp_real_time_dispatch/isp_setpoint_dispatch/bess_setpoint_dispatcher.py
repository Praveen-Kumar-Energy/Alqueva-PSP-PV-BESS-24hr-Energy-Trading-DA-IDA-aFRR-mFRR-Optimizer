"""
bess_setpoint_dispatcher.py — BESS charge/discharge setpoint for one ISP.

The battery trims the fast residual between the plant net target and what the PSP
units deliver. Setpoints respect rated power (PR-9) and the SOC window (PR-7); an
infeasible request is clamped to what the current SOC allows and reported.
"""
from __future__ import annotations

from typing import Tuple

from common_layer.configuration.plant_config import BESSConfig
from common_layer.physical_plant_models import BESSModel, BESSDispatch


class BESSSetpointDispatcher:
    def __init__(self, cfg: BESSConfig):
        self.cfg = cfg
        self.model = BESSModel(cfg)

    def setpoint(self, residual_mw: float, soc_mwh: float, dt_h: float
                 ) -> Tuple[BESSDispatch, float]:
        """Setpoint for a residual (+ discharge to grid, - charge).

        Returns (dispatch, new_soc_mwh). Power capped at rating; energy capped so
        SOC stays within [E_min, E_max]."""
        p = self.cfg.power_mw
        if residual_mw > 1e-6:                                  # discharge
            by_energy = max(0.0, (soc_mwh - self.model.e_min_mwh)) * self.cfg.eta_discharge / dt_h
            dis = min(residual_mw, p, by_energy)
            d = BESSDispatch(discharge_mw=dis)
        elif residual_mw < -1e-6:                               # charge
            by_energy = max(0.0, (self.model.e_max_mwh - soc_mwh)) / self.cfg.eta_charge / dt_h
            chg = min(-residual_mw, p, by_energy)
            d = BESSDispatch(charge_mw=chg)
        else:
            d = BESSDispatch()
        return d, self.model.next_energy_mwh(soc_mwh, d, dt_h)
