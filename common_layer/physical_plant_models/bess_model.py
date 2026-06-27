"""
bess_model.py — physics + validation for the 1 MW / 2 MWh battery.

Pure Python (no solver). Supplies SOC dynamics, power/energy limits, and a
deliverability check for aFRR (can the battery sustain an up-regulation for the
full activation time from its current state?).

Spec mapping:
  PR-7  SOC always within [soc_min, soc_max]  (0.20 .. 1.90 MWh)
  PR-8  never charge AND discharge in the same step
  PR-9  charge/discharge power never exceeds rated power (1.0 MW)
  INV-4 SOC continuity:
            E[t] = E[t-1] + eta_c * P_charge * dt  -  P_discharge * dt / eta_d
  PR-12 (aFRR) offered up-power deliverable for FAT from current SOC

SIGN CONVENTION at plant terminals:
    discharge = POSITIVE power to grid
    charge    = NEGATIVE power (consumption)
Internally we track magnitudes charge_mw >= 0 and discharge_mw >= 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from common_layer.configuration.plant_config import BESSConfig

EPS_MW = 1e-6
EPS_MWH = 1e-6


@dataclass
class BESSDispatch:
    """Battery dispatch in one period. Magnitudes, both >= 0."""
    charge_mw: float = 0.0
    discharge_mw: float = 0.0

    @property
    def net_mw(self) -> float:
        """+ discharge to grid, - charge from grid."""
        return self.discharge_mw - self.charge_mw


class BESSModel:
    def __init__(self, cfg: BESSConfig):
        self.cfg = cfg

    # -- limits -------------------------------------------------------------
    @property
    def power_mw(self) -> float:
        return self.cfg.power_mw

    @property
    def e_min_mwh(self) -> float:
        return self.cfg.e_min_mwh

    @property
    def e_max_mwh(self) -> float:
        return self.cfg.e_max_mwh

    def initial_energy_mwh(self) -> float:
        return self.cfg.initial_soc_frac * self.cfg.capacity_mwh

    # -- dynamics -----------------------------------------------------------
    def next_energy_mwh(self, e_prev_mwh: float, d: BESSDispatch, dt_h: float) -> float:
        """Apply INV-4 SOC continuity for one step of length dt_h hours.

        Charging adds eta_c * P_charge * dt (losses on the way in).
        Discharging removes P_discharge * dt / eta_d (losses on the way out)."""
        return (e_prev_mwh
                + self.cfg.eta_charge * d.charge_mw * dt_h
                - d.discharge_mw * dt_h / self.cfg.eta_discharge)

    def afrr_up_deliverable_mw(self, e_current_mwh: float) -> float:
        """Max sustained discharge (MW) deliverable for the full aFRR activation time (FAT).

        Up-regulation means discharging. The battery must sustain the offered power for
        the full FAT (5 min for PICASSO) without the SOC falling below e_min.
        Usable energy above the floor, derated by eta_d, spread over FAT hours:
            sustainable_mw = (E - E_min) * eta_d / FAT_h
        Capped at rated power (PR-9)."""
        fat_h = self.cfg.afrr_fat_min / 60.0
        usable_mwh = max(0.0, e_current_mwh - self.e_min_mwh) * self.cfg.eta_discharge
        sustainable_mw = usable_mwh / fat_h if fat_h > 0 else 0.0
        return min(self.cfg.power_mw, sustainable_mw)

    def afrr_dn_deliverable_mw(self, e_current_mwh: float) -> float:
        """Max sustained charge (MW) deliverable for the full aFRR activation time (FAT).

        Down-regulation means charging. Headroom to e_max limits capacity; divide by
        eta_charge to find the grid-side MW the battery can absorb for the full FAT:
            sustainable_mw = (E_max - E) / eta_c / FAT_h
        Capped at rated power."""
        fat_h = self.cfg.afrr_fat_min / 60.0
        headroom_mwh = max(0.0, self.e_max_mwh - e_current_mwh)
        sustainable_mw = (headroom_mwh / self.cfg.eta_charge) / fat_h if fat_h > 0 else 0.0
        return min(self.cfg.power_mw, sustainable_mw)

    # -- validation ---------------------------------------------------------
    def validate_dispatch(self, d: BESSDispatch, label: str = "") -> List[str]:
        v: List[str] = []
        tag = f"[{label}] " if label else ""
        # PR-8: no simultaneous charge and discharge.
        if d.charge_mw > EPS_MW and d.discharge_mw > EPS_MW:
            v.append(f"{tag}PR-8 BESS charge {d.charge_mw:.3f} and discharge "
                     f"{d.discharge_mw:.3f} MW in same step")
        # PR-9: power within rating.
        if d.charge_mw > self.cfg.power_mw + EPS_MW:
            v.append(f"{tag}PR-9 BESS charge {d.charge_mw:.3f} > {self.cfg.power_mw} MW")
        if d.discharge_mw > self.cfg.power_mw + EPS_MW:
            v.append(f"{tag}PR-9 BESS discharge {d.discharge_mw:.3f} > {self.cfg.power_mw} MW")
        if d.charge_mw < -EPS_MW or d.discharge_mw < -EPS_MW:
            v.append(f"{tag}negative BESS magnitude: charge={d.charge_mw}, discharge={d.discharge_mw}")
        return v

    def validate_soc(self, e_mwh: float, label: str = "") -> List[str]:
        """PR-7: SOC must stay within [e_min, e_max]."""
        v: List[str] = []
        tag = f"[{label}] " if label else ""
        if e_mwh < self.e_min_mwh - EPS_MWH:
            v.append(f"{tag}PR-7 SOC {e_mwh:.4f} MWh < min {self.e_min_mwh:.4f} MWh")
        if e_mwh > self.e_max_mwh + EPS_MWH:
            v.append(f"{tag}PR-7 SOC {e_mwh:.4f} MWh > max {self.e_max_mwh:.4f} MWh")
        return v

    def validate_trajectory(self, e_start_mwh: float,
                            schedule: List[BESSDispatch], dt_h: float) -> List[str]:
        """Validate a full SOC trajectory: each dispatch (PR-8/9) and each
        resulting SOC (PR-7), stepping with INV-4."""
        v: List[str] = []
        e = e_start_mwh
        v.extend(self.validate_soc(e, label="t0"))
        for t, d in enumerate(schedule, start=1):
            v.extend(self.validate_dispatch(d, label=f"t{t}"))
            e = self.next_energy_mwh(e, d, dt_h)
            v.extend(self.validate_soc(e, label=f"t{t}"))
        return v
