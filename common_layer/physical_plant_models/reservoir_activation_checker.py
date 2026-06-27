"""
reservoir_activation_checker.py — Pedrógão lower reservoir safety under activation.

The scheduled hourly dispatch already respects the reservoir bounds (modelled in
the MILP). But TSO-instructed activations add EXTRA generation/pumping on top of
the schedule, changing water flows beyond what the optimizer assumed. This checker:

  1. Starts from the initial state (same as the MILP).
  2. Replays every hour using the committed schedule.
  3. For each ISP within a generation hour, adds the net aFRR + mFRR activation.
  4. Checks that Pedrógão (lower) and Alqueva (upper) stay within bounds.

Why the lower reservoir is critical
-------------------------------------
When the PSP generates (upper → lower flow), Pedrógão fills. If a sustained
series of aFRR UP activations push generation above schedule in hours H07-H11
(already near full generation), water accumulates in Pedrógão faster than the
MILP expected. If the MILP was already close to the lower reservoir capacity
limit, activations could cause a violation.

Conversely, during pumping hours (lower → upper), DOWN activations reduce pumping,
leaving more water in Pedrógão (reducing Pedrógão utilization), which is safe.

Water flow conversion (simplified linear proportional model)
------------------------------------------------------------
  generation_mw → upper-to-lower flow_m3h:
      flow_m3h = net_gen_mw / total_turbine_max_mw × (q_turbine_max_m3h × n_units)

  pump_mw (magnitude) → lower-to-upper flow_m3h:
      flow_m3h = |net_pump_mw| / total_pump_max_mw × (q_pump_max_m3h × n_units)

Note: this proportional model differs slightly from the piecewise MILP flow curve.
It is conservative enough for a post-hoc safety check but may diverge at part-load.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from common_layer.configuration.config_loader import AppConfig


@dataclass
class ReservoirActivationResult:
    violations: List[str]
    hourly_lower_hm3: Dict[int, float]    # lower reservoir level after each hour
    hourly_upper_hm3: Dict[int, float]
    min_lower_hm3: float
    max_lower_hm3: float
    min_upper_hm3: float


def check_reservoir_during_activation(
    delivery_date: str,
    cfg: AppConfig,
    committed: Dict[int, float],
    activations_by_isp: Dict[int, Dict[str, float]],  # {isp: {up_mw, dn_mw}}
) -> ReservoirActivationResult:
    """Check reservoir bounds when reserve activations are added to the schedule.

    Parameters
    ----------
    committed            : {hour: net_mw} from PositionStore (+ gen, - pump)
    activations_by_isp   : {isp: {up_mw, dn_mw}} combined across all products

    Returns ReservoirActivationResult with violation strings and level history.
    """
    psp = cfg.plant.psp
    res = cfg.plant.reservoir

    # Plant-level flow capacity at full dispatch — used as proportional scale factors.
    turb_max_mw   = psp.total_turbine_max_mw                 # 518.4 MW
    pump_max_mw   = psp.total_pump_max_mw                    # 446.4 MW (magnitude)
    q_turb_max_m3h = psp.q_turbine_max_m3h * psp.n_units    # m3/h full-fleet turbine
    q_pump_max_m3h = psp.q_pump_max_m3h    * psp.n_units    # m3/h full-fleet pump

    upper_hm3 = res.upper_initial_hm3
    lower_hm3 = res.lower_initial_hm3

    lower_max_hm3 = res.lower_capacity_hm3
    lower_min_hm3 = res.lower_min_hm3
    upper_max_hm3 = res.upper_capacity_hm3
    upper_min_hm3 = res.upper_min_hm3

    violations: List[str] = []
    hourly_lower: Dict[int, float] = {}
    hourly_upper: Dict[int, float] = {}

    from common_layer.utilities import date_utils as du
    day = du.parse_date(delivery_date)
    isp_h = du.isp_duration_min(day) / 60.0
    month = day.month
    base_inflow_m3h = res.inflow_for_month(month)

    def net_flow_m3h(net_mw: float) -> float:
        """Upper-to-lower flow rate in m3/h (positive = upper losing water to lower).
        Generating (net_mw > 0): upper drains, lower fills.
        Pumping   (net_mw < 0): lower drains, upper fills (returns negative)."""
        if net_mw >= 0:
            gen_mw = min(net_mw, turb_max_mw)
            return gen_mw / turb_max_mw * q_turb_max_m3h if turb_max_mw > 0 else 0.0
        else:
            pump_mw = min(abs(net_mw), pump_max_mw)
            flow = pump_mw / pump_max_mw * q_pump_max_m3h if pump_max_mw > 0 else 0.0
            return -flow  # negative: lower → upper

    for h in sorted(committed):
        base_net = committed[h]
        isps = du.hour_to_isps(h, day)
        hour_upper = upper_hm3
        hour_lower = lower_hm3

        for isp in isps:
            act = activations_by_isp.get(isp, {})
            net_act_mw = act.get("up_mw", 0.0) - act.get("dn_mw", 0.0)
            effective_net = base_net + net_act_mw

            flow = net_flow_m3h(effective_net)              # m3/h upper→lower (signed)
            inflow = base_inflow_m3h / len(isps) * isp_h   # m3 natural inflow this ISP

            # Upper: loses water via turbine (flow > 0), gains via pump (flow < 0) + inflow.
            upper_delta = -flow * isp_h + inflow            # m3; negative when generating
            hour_upper = upper_hm3 + upper_delta / 1_000_000           # convert m3 → hm3
            hour_lower = lower_hm3 + (flow * isp_h) / 1_000_000       # lower fills during generation

        # Check bounds at end of each hour.
        if hour_lower > lower_max_hm3 + 1e-4:
            violations.append(
                f"H{h} Pedrogao lower overflow: {hour_lower:.4f} > cap {lower_max_hm3:.4f} hm3"
                f" (activation added net {net_act_mw:+.1f} MW)"
            )
        if hour_lower < lower_min_hm3 - 1e-4:
            violations.append(
                f"H{h} Pedrogao lower underflow: {hour_lower:.4f} < min {lower_min_hm3:.4f} hm3"
            )
        if hour_upper > upper_max_hm3 + 1e-4:
            violations.append(
                f"H{h} Alqueva upper overflow: {hour_upper:.4f} > cap {upper_max_hm3:.4f} hm3"
            )
        if hour_upper < upper_min_hm3 - 1e-4:
            violations.append(
                f"H{h} Alqueva upper underflow: {hour_upper:.4f} < min {upper_min_hm3:.4f} hm3"
            )

        hourly_lower[h] = hour_lower
        hourly_upper[h] = hour_upper
        upper_hm3 = hour_upper
        lower_hm3 = hour_lower

    return ReservoirActivationResult(
        violations=violations,
        hourly_lower_hm3=hourly_lower,
        hourly_upper_hm3=hourly_upper,
        min_lower_hm3=min(hourly_lower.values()) if hourly_lower else 0.0,
        max_lower_hm3=max(hourly_lower.values()) if hourly_lower else 0.0,
        min_upper_hm3=min(hourly_upper.values()) if hourly_upper else 0.0,
    )
