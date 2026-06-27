"""
core_milp_solver.py — solve the portfolio MILP and extract results.

Solver selection is automatic and CPLEX-first. The SAME Pyomo model is solved by
whichever solver in cfg.solver.fallback_order is installed, tried in order:

    1. CPLEX  (commercial, preferred) — via the CPLEX executable, no Python binding
    2. HiGHS  (free, open-source)     — via Pyomo APPSI (`pip install highspy`)
    3. CBC    (free, open-source)     — if a `cbc` executable is on PATH

So the pipeline runs on any machine: if CPLEX is installed it is used; if not, it
transparently falls back to a free solver. CPLEX always wins when both exist. A
one-line message prints which solver is active.

Enforces spec PR-13 / FR-1.6: if no solver reaches optimal or a usable feasible
solution within the gate time limit, this RAISES — no bid is ever built on an
unproven model.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pyomo.environ as pyo
from pyomo.core import Constraint
from pyomo.opt import SolverFactory, TerminationCondition, SolverStatus

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model.core_milp_builder import CoreModelMeta

# Termination conditions we accept as a usable solution (classic interface).
_OK_TERMS = {TerminationCondition.optimal, TerminationCondition.feasible,
             TerminationCondition.maxTimeLimit, TerminationCondition.locallyOptimal}
_BAD_TERMS = {TerminationCondition.infeasible,
              TerminationCondition.infeasibleOrUnbounded,
              TerminationCondition.unbounded}

# Announce the active solver once per process so users can see what is running.
_ANNOUNCED: set = set()


class SolveError(RuntimeError):
    """Raised when the MILP cannot be solved to a usable solution (PR-13)."""


def _announce(name: str) -> None:
    if name in _ANNOUNCED:
        return
    _ANNOUNCED.add(name)
    print({
        "cplex":       "[solver] CPLEX detected — using CPLEX (preferred).",
        "appsi_highs": "[solver] CPLEX not found — using HiGHS open-source fallback.",
        "cbc":         "[solver] CPLEX/HiGHS not found — using CBC open-source fallback.",
        "glpk":        "[solver] using GLPK open-source fallback.",
    }.get(name, f"[solver] using '{name}'."))


def _cplex_solver(cfg: AppConfig):
    """Configured CPLEX solver if its executable exists, else None."""
    exe = cfg.solver.resolve_executable()
    if exe is None:
        return None
    try:
        return SolverFactory("cplex", executable=exe)
    except Exception:
        return None


def _appsi_highs_solver():
    """HiGHS APPSI solver if available (needs `highspy` or a highs binary)."""
    try:
        opt = SolverFactory("appsi_highs")
        return opt if opt.available() else None
    except Exception:
        return None


def _classic_solver(name: str):
    """Classic executable solver (cbc/glpk) if available, else None."""
    try:
        opt = SolverFactory(name)
        return opt if opt.available(exception_flag=False) else None
    except Exception:
        return None


def _select_solver(cfg: AppConfig):
    """Walk cfg.solver.fallback_order; return (name, opt) for the first available
    solver, else None. Every supported solver exposes the classic Pyomo result API
    (HiGHS comes through Pyomo's legacy APPSI wrapper), so handling is uniform."""
    for raw in (cfg.solver.fallback_order or [cfg.solver.name]):
        name = str(raw).lower()
        if name == "cplex":
            opt = _cplex_solver(cfg)
            if opt is not None:
                return ("cplex", opt)
        elif name in ("appsi_highs", "highs"):
            opt = _appsi_highs_solver()
            if opt is not None:
                return ("appsi_highs", opt)
        elif name in ("cbc", "glpk"):
            opt = _classic_solver(name)
            if opt is not None:
                return (name, opt)
    return None


def _apply_options(opt, name: str, gap: float, tl: int, th: int) -> None:
    """Set optimality gap, time limit and threads using each solver's own keys."""
    if name == "cplex":
        opt.options["mipgap"] = gap
        opt.options["timelimit"] = tl
        if th > 0:
            opt.options["threads"] = th
    elif name == "appsi_highs":
        opt.config.time_limit = float(tl)
        opt.config.mip_gap = float(gap)
        opt.config.stream_solver = False
        if th > 0:
            opt.highs_options = {"threads": int(th)}
    elif name == "cbc":
        opt.options["ratioGap"] = gap
        opt.options["seconds"] = tl
        if th > 0:
            opt.options["threads"] = th
    elif name == "glpk":
        opt.options["mipgap"] = gap
        opt.options["tmlim"] = int(tl)


def solve_core_model(model: pyo.ConcreteModel, cfg: AppConfig, gate: str = "DA") -> float:
    """Solve the model in place with the first available solver (CPLEX preferred,
    then HiGHS, then CBC). Returns wall-clock solve seconds. Raises SolveError if no
    solver is installed or no usable solution is found (PR-13)."""
    sel = _select_solver(cfg)
    if sel is None:
        raise SolveError(
            "No optimisation solver available. Install IBM CPLEX (preferred) and set "
            "solver.executable in config/solver.yaml, or install a free fallback with "
            "`pip install highspy` (HiGHS). Tried: "
            f"{', '.join(cfg.solver.fallback_order or [cfg.solver.name])}. "
            "(PR-13: never bid on an unsolved model.)")

    name, opt = sel
    _announce(name)
    _apply_options(opt, name, cfg.solver.mip_gap,
                   cfg.solver.time_limit_for(gate), cfg.solver.threads)

    t0 = time.perf_counter()
    res = opt.solve(model, load_solutions=False)
    solve_time = time.perf_counter() - t0
    term = res.solver.termination_condition
    status = res.solver.status

    if term in _BAD_TERMS:
        raise SolveError(f"[{gate}] model {term} — physically infeasible inputs or "
                         f"over-constrained. No bid produced (PR-13).")
    if status == SolverStatus.ok or term in _OK_TERMS:
        if len(res.solution) == 0:
            raise SolveError(f"[{gate}] solver returned no solution (term={term}). "
                             f"No bid produced (PR-13).")
        model.solutions.load_from(res)
        return solve_time
    raise SolveError(f"[{gate}] unusable solver result: status={status}, term={term}. "
                     f"No bid produced (PR-13).")


@dataclass
class GateResults:
    da_bids: Dict[int, dict]            # {hour: {volume_mwh, price_eur_mwh}}
    net_position_mw: Dict[int, float]   # {hour: net MW}
    psp_schedule: Dict[int, dict]       # per-hour turbine/pump totals + per-unit
    bess_schedule: Dict[int, dict]
    pv_schedule: Dict[int, dict]
    reservoir_trajectory: Dict[int, dict]
    efficiency_per_hour: Dict[int, dict]  # {h: {eta_trb_pw, eta_pmp_pw}}
    energy_revenue_eur: float
    objective_eur: float


def _eta_pw(model: pyo.ConcreteModel, meta: CoreModelMeta, h: int, mode: str) -> float:
    """Power-weighted efficiency at hour h from omega interpolation weights.

    Derived from the solved bilinear interpolation: each omega weight carries
    its grid cell's efficiency and energy throughput. The power-weighted average
    gives the true realised efficiency at the operating point (flow × head).
    Returns 0.0 when the asset is off (no active omega weights).
    """
    v = pyo.value
    omega = model.omega_trb if mode == "trb" else model.omega_pmp
    eff   = meta.eff_trb    if mode == "trb" else meta.eff_pmp
    fg    = meta.flow_grid_trb if mode == "trb" else meta.flow_grid_pmp
    hg    = meta.head_grid
    FI    = range(len(fg))
    HI    = range(len(hg))
    num = den = 0.0
    for u in meta.units:
        for fi in FI:
            for hi in HI:
                w = v(omega[u, fi, hi, h])
                fh = fg[fi] * hg[hi]
                num += w * eff[(fi, hi)] * fh
                den += w * fh
    return num / den if den > 1e-12 else 0.0


def extract_results(model: pyo.ConcreteModel, meta: CoreModelMeta) -> GateResults:
    """Pull a solved model into plain dicts for checking, formatting, storage."""
    H, U, dt = meta.hours, meta.units, meta.dt_h
    v = pyo.value

    da_bids: Dict[int, dict] = {}
    net_pos: Dict[int, float] = {}
    psp_sched: Dict[int, dict] = {}
    bess_sched: Dict[int, dict] = {}
    pv_sched: Dict[int, dict] = {}
    res_traj: Dict[int, dict] = {}
    eff_ph: Dict[int, dict] = {}

    energy_rev = 0.0
    for h in H:
        p_net = v(model.p_net[h])
        net_pos[h] = p_net
        vol = p_net * dt
        price = meta.da_prices[h]
        da_bids[h] = {"volume_mwh": vol, "price_eur_mwh": price}
        energy_rev += price * vol

        psp_sched[h] = {
            "turbine_mw": sum(v(model.p_turb[u, h]) for u in U),
            "pump_mw": sum(v(model.p_pump[u, h]) for u in U),
            "units_turbine": [v(model.p_turb[u, h]) for u in U],
            "units_pump": [v(model.p_pump[u, h]) for u in U],
            "units_on_turb": [round(v(model.on_turb[u, h])) for u in U],
            "units_on_pump": [round(v(model.on_pump[u, h])) for u in U],
            "units_q_turb": [v(model.q_turb[u, h]) for u in U],
            "units_q_pump": [v(model.q_pump[u, h]) for u in U],
            "q_turb_total_m3h": sum(v(model.q_turb[u, h]) for u in U),
            "q_pump_total_m3h": sum(v(model.q_pump[u, h]) for u in U),
        }
        pv_to_bess = v(model.pv_to_bess[h])
        bess_sched[h] = {
            "charge_mw": v(model.p_chg[h]),
            "pv_to_bess_mw": pv_to_bess,
            "total_charge_mw": v(model.p_chg[h]) + pv_to_bess,
            "discharge_mw": v(model.p_dis[h]),
            "soc_mwh": v(model.soc[h]),
        }
        pv_sched[h] = {
            "used_mw": v(model.pv_used[h]),
            "to_bess_mw": pv_to_bess,
            "curtailed_mw": v(model.pv_curt[h]),
            "available_mw": meta.pv_available[h],
        }
        res_traj[h] = {
            "upper_hm3": v(model.v_up[h]),
            "lower_hm3": v(model.v_low[h]),
            "spill_m3h": v(model.spill[h]),
            "head_m": v(model.H_net[h]),
        }
        eff_ph[h] = {
            "eta_trb_pw": _eta_pw(model, meta, h, "trb"),
            "eta_pmp_pw": _eta_pw(model, meta, h, "pmp"),
        }

    return GateResults(
        da_bids=da_bids,
        net_position_mw=net_pos,
        psp_schedule=psp_sched,
        bess_schedule=bess_sched,
        pv_schedule=pv_sched,
        reservoir_trajectory=res_traj,
        efficiency_per_hour=eff_ph,
        energy_revenue_eur=energy_rev,
        objective_eur=v(model.objective),
    )


def analyze_binding_constraints(model: pyo.ConcreteModel,
                                threshold: float = 1e-6) -> List[dict]:
    """Post-solve: find constraints with slack ≈ 0 (physically active limits).

    Returns list of dicts {name, slack, binding} sorted by |slack| ascending.
    Dual values unavailable for MIPs (require LP relaxation); only slack reported.
    """
    rows: List[dict] = []
    for con in model.component_data_objects(Constraint, active=True):
        try:
            slack = con.slack()
        except Exception:
            continue
        if slack is None:
            continue
        rows.append({
            "name": con.name,
            "slack": round(float(slack), 6),
            "binding": abs(float(slack)) < threshold,
        })
    rows.sort(key=lambda r: abs(r["slack"]))
    return rows
