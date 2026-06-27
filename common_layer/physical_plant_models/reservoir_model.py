"""
reservoir_model.py — two-reservoir closed loop (Alqueva upper / Pedrógão lower).

Water moves between the two reservoirs:
    GENERATING (turbine): water leaves UPPER → enters LOWER.
    PUMPING:              water leaves LOWER → enters UPPER.
Natural inflow enters UPPER only. Spill leaves UPPER downstream (out of system).

Volumes tracked in hm3 (= Mm³ = 1e6 m3). Flows in m3/h. Volume change per step:
    ΔV [hm3] = flow_m3h × dt_h / 1,000,000

Head model (used for efficiency): H_net = 54.7 + 7.89e-9 × (V_up_m³ − 830e6) m
    → range 54.7 m (lower bound) to 73.0 m (upper usable capacity)

Spec mapping:
  INV-2  upper continuity: V[t] = V[t-1] + inflow − turbine_out + pump_in − spill
  INV-3  closed loop: turbine water leaving upper == entering lower;
                      pump water leaving lower == entering upper
  PR-5   upper volume within [upper_min, upper_usable]
  PR-6   lower volume within [lower_min, lower_capacity]
  PR-15  water balance holds every step (no water created or destroyed)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from common_layer.configuration.plant_config import ReservoirConfig

EPS_HM3 = 1e-6
M3_PER_HM3 = 1.0e6


@dataclass
class ReservoirFlows:
    """Flows during one period (m3/h, all >= 0)."""
    inflow_m3h: float = 0.0
    turbine_flow_m3h: float = 0.0   # upper -> lower (generation)
    pump_flow_m3h: float = 0.0      # lower -> upper (pumping)
    spill_m3h: float = 0.0          # upper -> downstream (out of system)


@dataclass
class ReservoirState:
    upper_hm3: float
    lower_hm3: float


class ReservoirModel:
    def __init__(self, cfg: ReservoirConfig):
        self.cfg = cfg

    # -- bounds -------------------------------------------------------------
    @property
    def upper_min_hm3(self) -> float:
        return self.cfg.upper_min_hm3

    @property
    def upper_max_hm3(self) -> float:
        return self.cfg.upper_usable_hm3

    @property
    def lower_min_hm3(self) -> float:
        return self.cfg.lower_min_hm3

    @property
    def lower_max_hm3(self) -> float:
        return self.cfg.lower_capacity_hm3

    def initial_state(self) -> ReservoirState:
        return ReservoirState(self.cfg.upper_initial_hm3, self.cfg.lower_initial_hm3)

    # -- dynamics -----------------------------------------------------------
    def step(self, state: ReservoirState, f: ReservoirFlows, dt_h: float) -> ReservoirState:
        """Advance both reservoirs one step (INV-2, INV-3, PR-15).

        Upper balance: + inflow + pump_in − turbine_out − spill
        Lower balance: + turbine_in − pump_out
        Closed loop (INV-3): every m3 leaving one reservoir arrives at the other."""
        d_turbine = f.turbine_flow_m3h * dt_h / M3_PER_HM3
        d_pump    = f.pump_flow_m3h    * dt_h / M3_PER_HM3
        d_inflow  = f.inflow_m3h       * dt_h / M3_PER_HM3
        d_spill   = f.spill_m3h        * dt_h / M3_PER_HM3

        upper_next = state.upper_hm3 + d_inflow + d_pump - d_turbine - d_spill
        lower_next = state.lower_hm3 + d_turbine - d_pump
        return ReservoirState(upper_next, lower_next)

    # -- validation ---------------------------------------------------------
    def validate_state(self, state: ReservoirState, label: str = "") -> List[str]:
        v: List[str] = []
        tag = f"[{label}] " if label else ""
        # PR-5: upper bounds.
        if state.upper_hm3 < self.upper_min_hm3 - EPS_HM3:
            v.append(f"{tag}PR-5 upper {state.upper_hm3:.3f} < min {self.upper_min_hm3:.1f} hm3")
        if state.upper_hm3 > self.upper_max_hm3 + EPS_HM3:
            v.append(f"{tag}PR-5 upper {state.upper_hm3:.3f} > usable {self.upper_max_hm3:.1f} hm3")
        # PR-6: Pedrógão lower bounds — binding constraint during long pumping sequences.
        if state.lower_hm3 < self.lower_min_hm3 - EPS_HM3:
            v.append(f"{tag}PR-6 lower {state.lower_hm3:.3f} < min {self.lower_min_hm3:.1f} hm3")
        if state.lower_hm3 > self.lower_max_hm3 + EPS_HM3:
            v.append(f"{tag}PR-6 lower {state.lower_hm3:.3f} > capacity {self.lower_max_hm3:.1f} hm3")
        return v

    def validate_trajectory(self, start: ReservoirState,
                            flows: List[ReservoirFlows], dt_h: float) -> List[str]:
        """Step through a flow schedule, validating bounds (PR-5/6) at each state
        and confirming water conservation across the pair (INV-3, PR-15)."""
        v: List[str] = []
        s = start
        v.extend(self.validate_state(s, label="t0"))
        for t, f in enumerate(flows, start=1):
            before_total = s.upper_hm3 + s.lower_hm3
            s = self.step(s, f, dt_h)
            after_total = s.upper_hm3 + s.lower_hm3
            # PR-15: the only net changes to the pair total are inflow in and spill out.
            expected_delta = (f.inflow_m3h - f.spill_m3h) * dt_h / M3_PER_HM3
            if abs((after_total - before_total) - expected_delta) > 1e-4:
                v.append(f"[t{t}] PR-15 water not conserved: pair delta "
                         f"{after_total - before_total:.6f} != inflow-spill "
                         f"{expected_delta:.6f} hm3")
            v.extend(self.validate_state(s, label=f"t{t}"))
        return v
