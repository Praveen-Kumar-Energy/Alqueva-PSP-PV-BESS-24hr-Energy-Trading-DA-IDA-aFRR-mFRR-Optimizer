"""
da_bid_checker.py — physical bid checker; runs after every gate's MILP solve.

Runs on every DA output before submission. It does NOT trust the solver: it
re-derives the physical dispatch from the solved schedule and replays it through
the same physical_plant_models the MILP claims to honour. Any prohibition or
invariant violation stops submission with a clear report.

This guard shares the implementation session's blind spots (per the spec) — it is
a runtime safety net, not a substitute for an independent adversarial review.

Checks (re-validated from the extracted schedule):
    INV-1   net = PSP + PV + BESS, and bid volume = net * dt
    PR-1/2/3/4   PSP per-unit + fleet (via PSPModel)
    PR-7/8/9 INV-4   BESS dispatch + SOC trajectory (via BESSModel)
    PR-5/6 INV-2/3 PR-15   reservoir trajectory (via ReservoirModel)
    PR-10   PV used <= available (via PVModel)
    bid price within OMIE technical bounds
"""
from __future__ import annotations

from typing import Dict, List

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model.core_milp_solver import GateResults
from common_layer.physical_plant_models import (
    PSPModel, UnitDispatch, BESSModel, BESSDispatch,
    ReservoirModel, ReservoirState, ReservoirFlows, PVModel,
)

EPS = 1e-4


class BidCheckError(ValueError):
    """Raised when a produced bid violates a physical prohibition/invariant."""


def check_da_bid(results: GateResults, inputs: dict, cfg: AppConfig,
                 gate: str = "DA") -> List[str]:
    """Validate a solved gate result. Returns [] if clean; raises BidCheckError
    (with the full violation list) otherwise."""
    plant = cfg.plant
    H = list(inputs["hours"])
    dt = float(inputs.get("dt_h", 1.0))
    year = int(inputs.get("delivery_date", "2026-01-01")[:4])

    psp = PSPModel(plant.psp)
    bess = BESSModel(plant.bess)
    reservoir = ReservoirModel(plant.reservoir)
    pv = PVModel(plant.pv, year=year)

    bl = cfg.market.bid_limits
    v: List[str] = []

    # Build per-hour reservoir flows to replay the trajectory.
    res_flows: List[ReservoirFlows] = []
    bess_dispatches: List[BESSDispatch] = []

    for h in H:
        ps = results.psp_schedule[h]
        bs = results.bess_schedule[h]
        pvs = results.pv_schedule[h]
        net = results.net_position_mw[h]
        bid = results.da_bids[h]

        # --- PSP per-unit + fleet (PR-1/2/3/4) ---
        units = [UnitDispatch(turbine_mw=ps["units_turbine"][u],
                              pump_mw=ps["units_pump"][u])
                 for u in range(plant.psp.n_units)]
        v.extend(psp.validate_plant(units, label=f"H{h}"))

        # --- PV (PR-10) ---
        v.extend(pv.validate_used_pv(pvs["used_mw"], pvs["available_mw"], label=f"H{h}"))

        # --- BESS dispatch (PR-8/9) ---
        bd = BESSDispatch(charge_mw=bs["charge_mw"], discharge_mw=bs["discharge_mw"])
        v.extend(bess.validate_dispatch(bd, label=f"H{h}"))
        bess_dispatches.append(bd)

        # --- INV-1 energy balance: net == PSP + PV + BESS ---
        psp_net = sum(u.net_mw for u in units)
        recomputed = psp_net + pvs["used_mw"] + bs["discharge_mw"] - bs["charge_mw"]
        if abs(recomputed - net) > EPS:
            v.append(f"H{h} INV-1 energy balance: net {net:.4f} != "
                     f"PSP+PV+BESS {recomputed:.4f} MW")
        if abs(bid["volume_mwh"] - net * dt) > EPS:
            v.append(f"H{h} INV-1 bid volume {bid['volume_mwh']:.4f} != net*dt "
                     f"{net * dt:.4f} MWh")

        # --- PR-4 / envelope ---
        if net > bl.max_generation_mw + EPS:
            v.append(f"H{h} PR-4 net {net:.2f} > max generation {bl.max_generation_mw} MW")
        if net < -bl.max_pump_mw - EPS:
            v.append(f"H{h} PR-4 net {net:.2f} < -max pump {-bl.max_pump_mw} MW")

        # --- bid price bounds ---
        pr = bid["price_eur_mwh"]
        if pr < bl.price_min_eur_mwh - EPS or pr > bl.price_max_eur_mwh + EPS:
            v.append(f"H{h} bid price {pr} outside OMIE bounds "
                     f"[{bl.price_min_eur_mwh}, {bl.price_max_eur_mwh}]")

        # collect reservoir flows for trajectory replay
        res_flows.append(ReservoirFlows(
            inflow_m3h=inputs.get("inflow_m3h", {}).get(h, 0.0),
            turbine_flow_m3h=sum(psp.turbine_flow_m3h(ps["units_turbine"][u])
                                 for u in range(plant.psp.n_units)),
            pump_flow_m3h=sum(psp.pump_flow_m3h(ps["units_pump"][u])
                              for u in range(plant.psp.n_units)),
            spill_m3h=results.reservoir_trajectory[h]["spill_m3h"],
        ))

    # --- BESS SOC trajectory (PR-7 + INV-4) ---
    init = inputs.get("initial_state", {})
    soc0 = float(init.get("bess_soc_frac", plant.bess.initial_soc_frac)) * plant.bess.capacity_mwh
    v.extend(bess.validate_trajectory(soc0, bess_dispatches, dt))

    # --- reservoir trajectory (PR-5/6, INV-2/3, PR-15) ---
    start = ReservoirState(
        upper_hm3=float(init.get("upper_reservoir_hm3", plant.reservoir.upper_initial_hm3)),
        lower_hm3=float(init.get("lower_reservoir_hm3", plant.reservoir.lower_initial_hm3)),
    )
    v.extend(reservoir.validate_trajectory(start, res_flows, dt))

    if v:
        raise BidCheckError(
            f"[{gate}] DA bid checker found {len(v)} violation(s):\n  - "
            + "\n  - ".join(v))
    return v
