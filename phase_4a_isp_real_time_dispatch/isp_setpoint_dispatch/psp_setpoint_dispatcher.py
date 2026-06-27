"""
psp_setpoint_dispatcher.py — turn a PSP net-power target into per-unit setpoints.

Real time needs concrete unit setpoints, not just a plant total. Given a PSP net
target (+ generate, - pump) this greedily commits the fewest units that can meet
it while honouring the min-stable-load band (PR-2) and pump band (PR-3), then
validates the result with PSPModel so an infeasible target is reported, never sent.
"""
from __future__ import annotations

import math
from typing import List

from common_layer.configuration.plant_config import PSPConfig
from common_layer.physical_plant_models import PSPModel, UnitDispatch


class PSPSetpointDispatcher:
    def __init__(self, cfg: PSPConfig):
        self.cfg = cfg
        self.model = PSPModel(cfg)

    def allocate(self, psp_net_mw: float) -> List[UnitDispatch]:
        """Allocate a PSP net target across units. Returns n_units dispatches."""
        n = self.cfg.n_units
        units = [UnitDispatch() for _ in range(n)]

        if psp_net_mw > 1e-6:                       # generate
            self._spread(units, psp_net_mw, n,
                         lo=self.cfg.p_turbine_min_mw, hi=self.cfg.p_turbine_max_mw,
                         attr="turbine_mw")
        elif psp_net_mw < -1e-6:                    # pump
            p_pump_min = self.cfg.p_pump_max_mw * (self.cfg.q_pump_min_m3h / self.cfg.q_pump_max_m3h)
            self._spread(units, -psp_net_mw, n,
                         lo=p_pump_min, hi=self.cfg.p_pump_max_mw, attr="pump_mw")
        return units

    @staticmethod
    def _spread(units: List[UnitDispatch], target: float, n: int,
                lo: float, hi: float, attr: str) -> None:
        """Commit the fewest units to cover `target`, each within [lo, hi]."""
        k = max(1, math.ceil(target / hi))
        k = min(k, n)
        # Reduce k while per-unit load would fall below the minimum stable load.
        while k > 1 and target / k < lo:
            k -= 1
        per = min(hi, max(lo, target / k))
        remaining = target
        for i in range(k):
            val = min(per, hi, remaining)
            if val < lo:                            # last sliver below min: fold into prior unit
                if i > 0:
                    prev = getattr(units[i - 1], attr)
                    setattr(units[i - 1], attr, min(hi, prev + val))
                break
            setattr(units[i], attr, val)
            remaining -= val

    def validate(self, units: List[UnitDispatch], label: str = "") -> List[str]:
        return self.model.validate_plant(units, label=label)

    @staticmethod
    def net_mw(units: List[UnitDispatch]) -> float:
        return sum(u.net_mw for u in units)
