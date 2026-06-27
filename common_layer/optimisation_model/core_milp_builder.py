"""
core_milp_builder.py — the shared 24h MILP for the whole portfolio.

ONE model serves DA and every IDA gate; they differ only in the price/forecast
inputs and (for IDA) which hours are frozen to the already-committed position.
This keeps the physics in exactly one place (DRY) so a constraint fix propagates
to every gate.

Decision variables per hour h (and unit u for PSP):
    p_turb[u,h]             turbine power (MW, >=0)
    p_pump[u,h]             pump power    (MW, >=0)
    on_turb[u,h]            binary: unit u generating at hour h
    on_pump[u,h]            binary: unit u pumping at hour h
    q_turb[u,h]             turbine flow (m³/h)
    q_pump[u,h]             pump flow    (m³/h)
    start_turb[u,h]         binary: turbine start this hour
    H_net[h]                hydraulic head (m) — dynamic, tied to v_up[h]
    H_net_active_trb[u,h]   McCormick aux ≈ H_net[h] × on_turb[u,h]
    H_net_active_pmp[u,h]   McCormick aux ≈ H_net[h] × on_pump[u,h]
    omega_trb[u,fi,hi,h]    bilinear interp. weights — 5×5 turbine eff. surface
    omega_pmp[u,fi,hi,h]    bilinear interp. weights — 5×5 pump eff. surface
    pv_used[h]              PV power to grid (MW)
    pv_to_bess[h]           PV power routed to BESS charge (MW)
    pv_curt[h]              PV curtailed (MW)
    p_chg[h]                BESS grid-charge power (MW)
    p_dis[h]                BESS discharge power   (MW)
    chg_on[h] / dis_on[h]  BESS charge/discharge binaries
    soc[h]                  BESS energy (MWh)
    v_up[h]                 upper reservoir volume (hm³)
    v_low[h]                lower reservoir volume (hm³)
    spill[h]                upper reservoir spill  (m³/h)
    p_net[h]                net plant grid injection (MW) = bid quantity

Efficiency surface (5×5 flow × head grid per unit per mode):
    eta = a0+a1·fn+a2·hn+a3·fn·hn+a4·fn²+a5·hn²  clipped to [0.85, 0.92]
    P_turb = Σ_{f,h_idx} ω_trb · η_trb · ρgQH / CONV
    P_pump = Σ_{f,h_idx} ω_pmp · (1/η_pmp) · ρgQH / CONV

McCormick linearisation of bilinear head×binary per unit per mode:
    H_net_active_trb[u,h] ≈ H_net[h] · on_turb[u,h]
    4 envelope constraints per unit per mode per hour.

Spec constraints:
    PR-1/INV-5   mode exclusivity per unit
    PR-2         turbine within [min stable load, max] when on
    PR-3         pump within [min, max] when on
    PR-7/INV-4   BESS SoC continuity + bounds
    PR-8         no simultaneous BESS charge/discharge
    PR-9         BESS power within rating
    PR-10 → pv_balance: pv_used + pv_to_bess + pv_curt = pv_av
    INV-1        p_net = PSP_net + pv_used + p_dis - p_chg
    INV-2/3      two-reservoir closed-loop continuity
    PR-5/6       reservoir volume bounds
    INV-7        FCR headroom reserved (never sold)
    McCormick    head-binary coupling (4 constraints × 2 modes × U units × H hours)
    head-vol     H_net linear in v_up
    omega-conv   Σ ω = on_binary (interpolation completeness)
    omega-flow   Σ ω·Q_grid = q_turb / q_pump
    omega-head   Σ ω·H_grid = H_net_active_trb / pmp
    omega-power  Σ ω·pwr_coeff = p_turb / p_pump
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pyomo.environ as pyo

from common_layer.configuration.config_loader import AppConfig

# ── Physical constants ────────────────────────────────────────────────────────
M3_PER_HM3 = 1.0e6          # m³ per hm³
RHO_WATER = 1000.0           # kg/m³
G_GRAVITY = 9.81             # m/s²
CONV_M3H_TO_MW = 3.6e9      # P_MW = η·ρ·g·Q[m³/h]·H[m] / CONV_M3H_TO_MW

# ── Alqueva head-volume geometry (confirmed: Alqueva II operational range) ────
H_MIN_OP = 54.7              # m — head at minimum operating reservoir level
H_MAX_OP = 73.0              # m — head at maximum operating reservoir level
# McCormick bounds: tight (= operating range) for strong envelope relaxation
MC_H_LO = H_MIN_OP
MC_H_HI = H_MAX_OP

# ── 5×5 turbine/pump efficiency polynomial (Francis reversible units) ─────────
# eta = a0 + a1·fn + a2·hn + a3·fn·hn + a4·fn² + a5·hn²
# fn = normalised flow [0,1],  hn = normalised head [0,1]
COEFFS_TRB = [0.850, 0.030, 0.025, -0.008, -0.015, -0.012]
COEFFS_PMP = [0.850, 0.025, 0.020, -0.006, -0.012, -0.010]
ETA_LO, ETA_HI = 0.85, 0.92   # realistic bounds for Francis machines

N_GRID = 5                   # number of flow and head grid points each axis


@dataclass
class CoreModelMeta:
    """Side information the extractor needs after a solve."""
    hours: List[int]
    units: List[int]
    dt_h: float
    da_prices: Dict[int, float]
    pv_available: Dict[int, float]
    mwh_per_hm3: float
    flow_grid_trb: List[float]          # 5 turbine flow grid points (m³/h)
    flow_grid_pmp: List[float]          # 5 pump flow grid points (m³/h)
    head_grid: List[float]              # 5 head grid points (m)
    eff_trb: Dict[tuple, float]         # {(fi, hi): eta} turbine efficiency surface
    eff_pmp: Dict[tuple, float]         # {(fi, hi): eta} pump efficiency surface


# ── Helpers ───────────────────────────────────────────────────────────────────

def _efficiency_surface(flow_grid: List[float], head_grid: List[float],
                        coeffs: List[float]) -> Dict[tuple, float]:
    """Return {(fi, hi): eta} for each grid cell, clipped to [ETA_LO, ETA_HI]."""
    f_lo, f_hi = flow_grid[0], flow_grid[-1]
    h_lo, h_hi = head_grid[0], head_grid[-1]
    table: Dict[tuple, float] = {}
    a0, a1, a2, a3, a4, a5 = coeffs
    for fi, f in enumerate(flow_grid):
        fn = (f - f_lo) / (f_hi - f_lo) if f_hi > f_lo else 0.0
        for hi, h in enumerate(head_grid):
            hn = (h - h_lo) / (h_hi - h_lo) if h_hi > h_lo else 0.0
            eta = a0 + a1*fn + a2*hn + a3*fn*hn + a4*fn**2 + a5*hn**2
            table[(fi, hi)] = max(ETA_LO, min(eta, ETA_HI))
    return table


def build_core_model(
    inputs: dict,
    cfg: AppConfig,
    fixed_net_position: Optional[Dict[int, float]] = None,
) -> tuple[pyo.ConcreteModel, CoreModelMeta]:
    """Build the portfolio MILP.

    Args:
        inputs: {
            'hours':           [1..N],
            'dt_h':            1.0,
            'da_prices':       {h: EUR/MWh},
            'pv_available_mw': {h: MW},
            'inflow_m3h':      {h: m³/h},
            'initial_state':   {upper_reservoir_hm3, lower_reservoir_hm3, bess_soc_frac},
        }
        cfg: AppConfig.
        fixed_net_position: optional {h: MW} — freeze p_net for those hours
            (IDA gates use this to hold hours they cannot re-trade).

    Returns (model, meta).
    """
    p = cfg.plant
    psp, bess, res, econ = p.psp, p.bess, p.reservoir, p.economics

    H: List[int] = list(inputs["hours"])
    U: List[int] = list(range(1, psp.n_units + 1))
    dt = float(inputs.get("dt_h", 1.0))
    price = inputs["da_prices"]
    pv_av = inputs["pv_available_mw"]
    inflow = inputs.get("inflow_m3h", {h: 0.0 for h in H})
    init = inputs.get("initial_state", {})

    v_up0 = float(init.get("upper_reservoir_hm3", res.upper_initial_hm3))
    v_low0 = float(init.get("lower_reservoir_hm3", res.lower_initial_hm3))
    soc0 = float(init.get("bess_soc_frac", bess.initial_soc_frac)) * bess.capacity_mwh

    # Approx MWh per hm³ at rated conditions (used for terminal water value).
    mwh_per_hm3 = psp.p_turbine_max_mw / psp.q_turbine_max_m3h * M3_PER_HM3

    # FCR mandatory headroom (INV-7): reduce usable generation/pump envelope.
    fcr = max(0.0, p.fcr.mandatory_headroom_mw)
    p_gen_cap = p.p_max_generation_mw - fcr
    p_pump_cap = p.p_max_pump_mw - fcr

    # Pump minimum power derived from minimum flow fraction (per unit).
    p_pump_min_mw = psp.p_pump_max_mw * (psp.q_pump_min_m3h / psp.q_pump_max_m3h)

    # ── Head-volume linear geometry ───────────────────────────────────────────
    # H_net[h] = H_MIN_OP + dH_dQ * (v_up[h]*M3_PER_HM3 - Q_ref_m3)
    Q_ref_m3 = res.upper_min_hm3 * M3_PER_HM3           # volume at H_MIN_OP
    Q_range_m3 = (res.upper_usable_hm3 - res.upper_min_hm3) * M3_PER_HM3
    dH_dQ = (H_MAX_OP - H_MIN_OP) / Q_range_m3 if Q_range_m3 > 0 else 0.0

    # ── 5×5 efficiency surface ────────────────────────────────────────────────
    FI = list(range(N_GRID))
    HI = list(range(N_GRID))

    flow_grid_trb: List[float] = list(
        np.linspace(psp.q_turbine_min_m3h, psp.q_turbine_max_m3h, N_GRID))
    flow_grid_pmp: List[float] = list(
        np.linspace(psp.q_pump_min_m3h, psp.q_pump_max_m3h, N_GRID))
    head_grid: List[float] = list(
        np.linspace(H_MIN_OP, H_MAX_OP, N_GRID))

    eff_trb = _efficiency_surface(flow_grid_trb, head_grid, COEFFS_TRB)
    eff_pmp = _efficiency_surface(flow_grid_pmp, head_grid, COEFFS_PMP)

    # Precomputed MW coefficient per (fi, hi) cell — constant in the MILP.
    # TRB: P = eta * rho * g * Q[m³/h] * H[m] / CONV
    pwr_trb: Dict[tuple, float] = {
        (fi, hi): eff_trb[(fi, hi)] * RHO_WATER * G_GRAVITY
                  * flow_grid_trb[fi] * head_grid[hi] / CONV_M3H_TO_MW
        for fi in FI for hi in HI
    }
    # PMP: P = (1/eta) * rho * g * Q * H / CONV  (electrical power consumed)
    pwr_pmp: Dict[tuple, float] = {
        (fi, hi): (1.0 / eff_pmp[(fi, hi)]) * RHO_WATER * G_GRAVITY
                  * flow_grid_pmp[fi] * head_grid[hi] / CONV_M3H_TO_MW
        for fi in FI for hi in HI
    }

    # ── Build model ───────────────────────────────────────────────────────────
    m = pyo.ConcreteModel("alqueva_portfolio_24h")
    m.H = pyo.Set(initialize=H, ordered=True)
    m.U = pyo.Set(initialize=U, ordered=True)
    m.FI = pyo.Set(initialize=FI, ordered=True)   # flow grid indices  0..4
    m.HI = pyo.Set(initialize=HI, ordered=True)   # head grid indices  0..4
    m.K4 = pyo.Set(initialize=[0, 1, 2, 3])       # McCormick envelope index

    first = H[0]

    def prev(h: int) -> int:
        return H[H.index(h) - 1]

    # ── PSP decision variables ────────────────────────────────────────────────
    m.p_turb = pyo.Var(m.U, m.H, domain=pyo.NonNegativeReals)
    m.p_pump = pyo.Var(m.U, m.H, domain=pyo.NonNegativeReals)
    m.on_turb = pyo.Var(m.U, m.H, domain=pyo.Binary)
    m.on_pump = pyo.Var(m.U, m.H, domain=pyo.Binary)
    m.q_turb = pyo.Var(m.U, m.H, domain=pyo.NonNegativeReals)   # m³/h
    m.q_pump = pyo.Var(m.U, m.H, domain=pyo.NonNegativeReals)   # m³/h
    m.start_turb = pyo.Var(m.U, m.H, domain=pyo.Binary)

    # Head and McCormick auxiliaries
    m.H_net = pyo.Var(m.H, domain=pyo.Reals,
                      bounds=(MC_H_LO, MC_H_HI))
    m.H_net_active_trb = pyo.Var(m.U, m.H, domain=pyo.Reals)
    m.H_net_active_pmp = pyo.Var(m.U, m.H, domain=pyo.Reals)

    # Bilinear interpolation weights — efficiency surface
    m.omega_trb = pyo.Var(m.U, m.FI, m.HI, m.H, domain=pyo.NonNegativeReals)
    m.omega_pmp = pyo.Var(m.U, m.FI, m.HI, m.H, domain=pyo.NonNegativeReals)

    # ── PV / BESS / reservoir variables ──────────────────────────────────────
    m.pv_used = pyo.Var(m.H, domain=pyo.NonNegativeReals)
    m.pv_to_bess = pyo.Var(m.H, domain=pyo.NonNegativeReals)   # PV → BESS
    m.pv_curt = pyo.Var(m.H, domain=pyo.NonNegativeReals)      # PV curtailed
    m.p_chg = pyo.Var(m.H, domain=pyo.NonNegativeReals)        # grid → BESS
    m.p_dis = pyo.Var(m.H, domain=pyo.NonNegativeReals)
    m.chg_on = pyo.Var(m.H, domain=pyo.Binary)
    m.dis_on = pyo.Var(m.H, domain=pyo.Binary)
    m.soc = pyo.Var(m.H, domain=pyo.NonNegativeReals)
    m.v_up = pyo.Var(m.H, domain=pyo.NonNegativeReals)
    m.v_low = pyo.Var(m.H, domain=pyo.NonNegativeReals)
    m.spill = pyo.Var(m.H, domain=pyo.NonNegativeReals)    # m³/h
    m.p_net = pyo.Var(m.H, domain=pyo.Reals)

    # ── PSP CONSTRAINTS ───────────────────────────────────────────────────────
    # PR-1 / INV-5: mode exclusivity per unit.
    m.mode_excl = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h: mm.on_turb[u, h] + mm.on_pump[u, h] <= 1)

    # PR-2: turbine min stable load / nameplate cap (retained as cut alongside omega).
    m.turb_max = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h: mm.p_turb[u, h] <= psp.p_turbine_max_mw * mm.on_turb[u, h])
    m.turb_min = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h: mm.p_turb[u, h] >= psp.p_turbine_min_mw * mm.on_turb[u, h])

    # PR-3: pump cap / min.
    m.pump_max = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h: mm.p_pump[u, h] <= psp.p_pump_max_mw * mm.on_pump[u, h])
    m.pump_min = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h: mm.p_pump[u, h] >= p_pump_min_mw * mm.on_pump[u, h])

    # Turbine start detection (for startup cost).
    def _start_rule(mm, u, h):
        if h == first:
            return mm.start_turb[u, h] >= mm.on_turb[u, h]
        return mm.start_turb[u, h] >= mm.on_turb[u, h] - mm.on_turb[u, prev(h)]
    m.turb_start = pyo.Constraint(m.U, m.H, rule=_start_rule)

    # ── HEAD-VOLUME RELATIONSHIP ──────────────────────────────────────────────
    # Dynamic head: H_net[h] = H_MIN_OP + dH_dQ * (v_up[h]*M3_PER_HM3 - Q_ref)
    m.head_vol = pyo.Constraint(m.H,
        rule=lambda mm, h: mm.H_net[h] == (H_MIN_OP
            + dH_dQ * (mm.v_up[h] * M3_PER_HM3 - Q_ref_m3)))

    # ── McCORMICK LINEARISATION ───────────────────────────────────────────────
    # Linearises bilinear H_net[h] * on_turb[u,h] → H_net_active_trb[u,h].
    # Four envelope constraints form the tightest convex relaxation (McCormick 1976).
    def _mc_trb(mm, u, h, k):
        z, x = mm.H_net_active_trb[u, h], mm.on_turb[u, h]
        Hn = mm.H_net[h]
        if k == 0: return z <= MC_H_HI * x
        if k == 1: return z >= MC_H_LO * x
        if k == 2: return z <= Hn - MC_H_LO * (1 - x)
        return              z >= Hn - MC_H_HI * (1 - x)

    def _mc_pmp(mm, u, h, k):
        z, x = mm.H_net_active_pmp[u, h], mm.on_pump[u, h]
        Hn = mm.H_net[h]
        if k == 0: return z <= MC_H_HI * x
        if k == 1: return z >= MC_H_LO * x
        if k == 2: return z <= Hn - MC_H_LO * (1 - x)
        return              z >= Hn - MC_H_HI * (1 - x)

    m.mc_trb = pyo.Constraint(m.U, m.H, m.K4, rule=_mc_trb)
    m.mc_pmp = pyo.Constraint(m.U, m.H, m.K4, rule=_mc_pmp)

    # ── OMEGA — BILINEAR EFFICIENCY SURFACE INTERPOLATION ────────────────────

    # Convexity: weights sum to binary status (zero when off, one when on).
    m.omega_trb_conv = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h:
            sum(mm.omega_trb[u, fi, hi, h] for fi in FI for hi in HI)
            == mm.on_turb[u, h])
    m.omega_pmp_conv = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h:
            sum(mm.omega_pmp[u, fi, hi, h] for fi in FI for hi in HI)
            == mm.on_pump[u, h])

    # Power from efficiency surface: Σ ω·pwr_coeff = p_turb / p_pump.
    m.omega_trb_pwr = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h:
            mm.p_turb[u, h] == sum(
                mm.omega_trb[u, fi, hi, h] * pwr_trb[(fi, hi)]
                for fi in FI for hi in HI))
    m.omega_pmp_pwr = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h:
            mm.p_pump[u, h] == sum(
                mm.omega_pmp[u, fi, hi, h] * pwr_pmp[(fi, hi)]
                for fi in FI for hi in HI))

    # Flow from omega (used in reservoir water balance).
    m.omega_trb_flow = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h:
            mm.q_turb[u, h] == sum(
                mm.omega_trb[u, fi, hi, h] * flow_grid_trb[fi]
                for fi in FI for hi in HI))
    m.omega_pmp_flow = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h:
            mm.q_pump[u, h] == sum(
                mm.omega_pmp[u, fi, hi, h] * flow_grid_pmp[fi]
                for fi in FI for hi in HI))

    # Head coupling via McCormick auxiliary — links omega weights to dynamic head.
    # Σ ω·H_grid = H_net_active = H_net * on_binary  (linearised via McCormick)
    m.omega_trb_head = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h:
            sum(mm.omega_trb[u, fi, hi, h] * head_grid[hi]
                for fi in FI for hi in HI)
            == mm.H_net_active_trb[u, h])
    m.omega_pmp_head = pyo.Constraint(m.U, m.H,
        rule=lambda mm, u, h:
            sum(mm.omega_pmp[u, fi, hi, h] * head_grid[hi]
                for fi in FI for hi in HI)
            == mm.H_net_active_pmp[u, h])

    # ── BESS CONSTRAINTS ─────────────────────────────────────────────────────
    # PR-8: no simultaneous charge and discharge.
    m.bess_excl = pyo.Constraint(m.H,
        rule=lambda mm, h: mm.chg_on[h] + mm.dis_on[h] <= 1)

    # PR-9: power within rating.
    # Total BESS charge = grid-charge (p_chg) + PV-routed charge (pv_to_bess).
    m.chg_cap = pyo.Constraint(m.H,
        rule=lambda mm, h:
            mm.p_chg[h] + mm.pv_to_bess[h] <= bess.power_mw * mm.chg_on[h])
    m.dis_cap = pyo.Constraint(m.H,
        rule=lambda mm, h: mm.p_dis[h] <= bess.power_mw * mm.dis_on[h])

    # PV→BESS within BESS power rating.
    m.pv_to_bess_cap = pyo.Constraint(m.H,
        rule=lambda mm, h: mm.pv_to_bess[h] <= bess.power_mw)

    # INV-4: SoC continuity — both charge paths contribute.
    def _soc_rule(mm, h):
        prev_e = soc0 if h == first else mm.soc[prev(h)]
        total_chg = mm.p_chg[h] + mm.pv_to_bess[h]
        return mm.soc[h] == (prev_e
                             + bess.eta_charge * total_chg * dt
                             - mm.p_dis[h] * dt / bess.eta_discharge)
    m.soc_balance = pyo.Constraint(m.H, rule=_soc_rule)

    # PR-7: SoC bounds.
    m.soc_lo = pyo.Constraint(m.H, rule=lambda mm, h: mm.soc[h] >= bess.e_min_mwh)
    m.soc_hi = pyo.Constraint(m.H, rule=lambda mm, h: mm.soc[h] <= bess.e_max_mwh)

    # ── PV ENERGY BALANCE (PR-10 extended) ───────────────────────────────────
    # Explicit allocation: to grid + to BESS + curtailed = available.
    # (Replaces previous one-sided cap pv_used <= pv_av.)
    m.pv_balance = pyo.Constraint(m.H,
        rule=lambda mm, h:
            mm.pv_used[h] + mm.pv_to_bess[h] + mm.pv_curt[h] == pv_av[h])

    # ── RESERVOIR CONSTRAINTS ─────────────────────────────────────────────────
    # INV-2/3: two-reservoir closed-loop continuity.
    def _vup_rule(mm, h):
        prev_v = v_up0 if h == first else mm.v_up[prev(h)]
        net_flow = (inflow[h]
                    + sum(mm.q_pump[u, h] for u in U)
                    - sum(mm.q_turb[u, h] for u in U)
                    - mm.spill[h])
        return mm.v_up[h] == prev_v + net_flow * dt / M3_PER_HM3

    def _vlow_rule(mm, h):
        prev_v = v_low0 if h == first else mm.v_low[prev(h)]
        net_flow = (sum(mm.q_turb[u, h] for u in U)
                    - sum(mm.q_pump[u, h] for u in U))
        return mm.v_low[h] == prev_v + net_flow * dt / M3_PER_HM3

    m.vup_balance = pyo.Constraint(m.H, rule=_vup_rule)
    m.vlow_balance = pyo.Constraint(m.H, rule=_vlow_rule)

    # PR-5: upper reservoir bounds.
    m.vup_lo = pyo.Constraint(m.H,
        rule=lambda mm, h: mm.v_up[h] >= res.upper_min_hm3)
    m.vup_hi = pyo.Constraint(m.H,
        rule=lambda mm, h: mm.v_up[h] <= res.upper_usable_hm3)
    # PR-6: lower (Pedrógão) reservoir bounds.
    m.vlow_lo = pyo.Constraint(m.H,
        rule=lambda mm, h: mm.v_low[h] >= res.lower_min_hm3)
    m.vlow_hi = pyo.Constraint(m.H,
        rule=lambda mm, h: mm.v_low[h] <= res.lower_capacity_hm3)

    # Terminal reservoir hard constraint: end-of-day upper reservoir >= initial.
    # Prevents day-by-day depletion when water value calibration is imperfect.
    m.terminal_reservoir = pyo.Constraint(expr=m.v_up[H[-1]] >= v_up0)

    # ── ENERGY BALANCE (INV-1) ────────────────────────────────────────────────
    # p_net = PSP net + PV to grid + BESS discharge - BESS grid-charge.
    # Note: pv_to_bess is internal (PV → BESS), does NOT cross the grid boundary.
    def _pnet_rule(mm, h):
        psp_net = sum(mm.p_turb[u, h] - mm.p_pump[u, h] for u in U)
        return mm.p_net[h] == psp_net + mm.pv_used[h] + mm.p_dis[h] - mm.p_chg[h]
    m.pnet_balance = pyo.Constraint(m.H, rule=_pnet_rule)

    # PR-4 / INV-7: net within FCR-reduced envelope.
    m.pnet_hi = pyo.Constraint(m.H,
        rule=lambda mm, h: mm.p_net[h] <= p_gen_cap)
    m.pnet_lo = pyo.Constraint(m.H,
        rule=lambda mm, h: mm.p_net[h] >= -p_pump_cap)

    # Freeze hours that an IDA gate cannot re-trade.
    if fixed_net_position:
        m.fixed_net = pyo.Constraint(
            [h for h in H if h in fixed_net_position],
            rule=lambda mm, h: mm.p_net[h] == fixed_net_position[h])

    # ── OBJECTIVE ─────────────────────────────────────────────────────────────
    def _objective(mm):
        energy_rev = sum(price[h] * mm.p_net[h] * dt for h in H)
        water_val = econ.water_value_eur_mwh * mwh_per_hm3 * (mm.v_up[H[-1]] - v_up0)
        pv_pen = econ.pv_curtailment_penalty_eur_mwh * sum(
            mm.pv_curt[h] * dt for h in H)
        bess_deg = bess.degradation_cost_eur_mwh * sum(
            (mm.p_chg[h] + mm.pv_to_bess[h] + mm.p_dis[h]) * dt for h in H)
        spill_pen = econ.spillage_penalty_eur_m3 * sum(mm.spill[h] * dt for h in H)
        start_pen = psp.startup_cost_eur * sum(
            mm.start_turb[u, h] for u in U for h in H)
        return energy_rev + water_val - pv_pen - bess_deg - spill_pen - start_pen

    m.objective = pyo.Objective(rule=_objective, sense=pyo.maximize)

    meta = CoreModelMeta(
        hours=H,
        units=U,
        dt_h=dt,
        da_prices=dict(price),
        pv_available=dict(pv_av),
        mwh_per_hm3=mwh_per_hm3,
        flow_grid_trb=flow_grid_trb,
        flow_grid_pmp=flow_grid_pmp,
        head_grid=head_grid,
        eff_trb=eff_trb,
        eff_pmp=eff_pmp,
    )
    return m, meta
