"""
milp_solution_quality_checker.py — independent MILP solve-quality probe.

Rebuilds and solves the core model for a date and reports feasibility, objective,
and solve time, then re-runs the Phase 3A bid checker on the extracted schedule.
Used by the backtest to confirm every replayed day solved cleanly and produced a
physically valid plan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model import (
    build_core_model, solve_core_model, extract_results, SolveError,
)
from common_layer.optimisation_model.core_milp_solver import GateResults
from phase_1_da_day_ahead_bidding.da_bid_formatting.da_bid_checker import (
    check_da_bid, BidCheckError,
)
from phase_5d_analytics_and_reporting.analytics_and_kpis.operational_analytics import (
    compute_operational_patterns,
    compute_temporal_patterns,
    compute_economic_kpis_extended,
)


@dataclass
class SolveQuality:
    feasible: bool
    objective_eur: float
    solve_time_sec: float
    checker_passed: bool
    note: str = ""
    gate_results: Optional[GateResults] = field(default=None, repr=False)
    operational: dict = field(default_factory=dict)
    temporal: dict = field(default_factory=dict)
    economic_ext: dict = field(default_factory=dict)


def check_solution_quality(inputs: dict, cfg: AppConfig) -> SolveQuality:
    try:
        model, meta = build_core_model(inputs, cfg)
        st = solve_core_model(model, cfg, gate="DA")
    except SolveError as e:
        return SolveQuality(False, 0.0, 0.0, False, note=str(e))
    results = extract_results(model, meta)
    try:
        check_da_bid(results, inputs, cfg, gate="DA")
        checker_ok = True
        note = ""
    except BidCheckError as e:
        checker_ok = False
        note = str(e).splitlines()[0]

    ops = compute_operational_patterns(
        results.psp_schedule, results.bess_schedule, results.net_position_mw)
    tmp = compute_temporal_patterns(
        results.psp_schedule, results.pv_schedule, inputs["da_prices"])
    p   = cfg.plant
    eco = compute_economic_kpis_extended(
        psp_schedule=results.psp_schedule,
        bess_schedule=results.bess_schedule,
        pv_schedule=results.pv_schedule,
        reservoir_trajectory=results.reservoir_trajectory,
        efficiency_per_hour=results.efficiency_per_hour,
        da_prices=inputs["da_prices"],
        energy_revenue_eur=results.energy_revenue_eur,
        reserve_revenue_eur=0.0,   # reserve settled separately; 0 for DA-only solve
        p_turbine_max_mw=p.p_max_generation_mw,
        p_pump_max_mw=p.p_max_pump_mw,
        bess_power_mw=p.bess.power_mw,
        upper_usable_hm3=p.reservoir.upper_usable_hm3,
        upper_min_hm3=p.reservoir.upper_min_hm3,
        dt_h=meta.dt_h,
    )

    return SolveQuality(
        feasible=True,
        objective_eur=results.objective_eur,
        solve_time_sec=st,
        checker_passed=checker_ok,
        note=note,
        gate_results=results,
        operational=ops,
        temporal=tmp,
        economic_ext=eco,
    )
