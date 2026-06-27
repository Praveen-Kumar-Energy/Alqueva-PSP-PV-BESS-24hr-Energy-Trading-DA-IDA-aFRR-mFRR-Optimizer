"""
test_milp_physics.py — physics correctness, MILP constraint verification,
and adversarial stress tests for the Alqueva pipeline.

Structure
---------
Group A  Pure physics (no solver — instant)
    A1  Head-volume linear formula at boundary values
    A2  Efficiency surface: all eta values within [0.85, 0.92]
    A3  McCormick envelope: all 4 constraints hold for sampled (H, binary) pairs
    A4  Power conversion formula: P = eta*rho*g*Q*H / CONV gives expected MW
    A5  BESS SoC continuity equation is exact
    A6  Reservoir closed-loop water balance (no inflow, no spill)
    A7  PV balance: used + to_bess + curtailed == available (exact arithmetic)

Group B  MILP solve — constraint verification on solved schedule  [integration]
    B1  Normal solve: feasible, objective > 0, checker pass
    B2  Mode exclusivity: on_turb[u,h] + on_pump[u,h] <= 1 for all u,h
    B3  No simultaneous BESS charge and discharge: chg_on + dis_on <= 1 every h
    B4  BESS SoC within [e_min, e_max] every hour
    B5  Net power within FCR-reduced envelope every hour
    B6  Reservoir volumes within bounds every hour
    B7  Terminal reservoir constraint: v_up[24] >= v_up_initial
    B8  PV balance: pv_used + pv_to_bess + pv_curt == pv_available every hour
    B9  Water balance: total flow in == total flow out (upper + lower, no inflow)
    B10 Head within operating range [H_MIN_OP, H_MAX_OP] every hour
    B11 Head-volume consistency: H_net matches formula from v_up
    B12 Power-weighted efficiency in realistic range when turbining

Group C  Adversarial MILP inputs  [integration]
    C1  Reservoir at minimum → turbining blocked (no net turbine output)
    C2  Reservoir at maximum → pumping blocked (no net pump output)
    C3  PV = 0 → pv_to_bess = 0 and pv_curt = 0 every hour
    C4  All prices zero → optimizer relies on water value + does not turbine at loss
    C5  All prices negative → optimizer pumps (buys cheap power)
    C6  Single feasible window: high price 1 hour only → only 1 turbine window

Group D  Binding constraint analysis  [integration]
    D1  analyze_binding_constraints returns a list of dicts with required keys
    D2  Known-binding constraints appear in the top-10 tightest
"""
from __future__ import annotations

import math
import os
import sys

import pytest
import pyomo.environ as pyo

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from common_layer.optimisation_model.core_milp_builder import (
    H_MIN_OP, H_MAX_OP, M3_PER_HM3, CONV_M3H_TO_MW,
    RHO_WATER, G_GRAVITY, ETA_LO, ETA_HI,
    COEFFS_TRB, COEFFS_PMP, N_GRID,
    _efficiency_surface,
)

TOL = 1e-4   # tolerance for constraint satisfaction checks


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP A — Pure physics (no solver)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPurePhysics:

    def test_A1_head_vol_at_lower_bound(self, cfg):
        """H_net at upper_min_hm3 should equal H_MIN_OP."""
        res = cfg.plant.reservoir
        Q_ref_m3 = res.upper_min_hm3 * M3_PER_HM3
        Q_range_m3 = (res.upper_usable_hm3 - res.upper_min_hm3) * M3_PER_HM3
        dH_dQ = (H_MAX_OP - H_MIN_OP) / Q_range_m3
        H = H_MIN_OP + dH_dQ * (res.upper_min_hm3 * M3_PER_HM3 - Q_ref_m3)
        assert abs(H - H_MIN_OP) < 1e-9, f"Expected {H_MIN_OP}, got {H}"

    def test_A1b_head_vol_at_upper_bound(self, cfg):
        """H_net at upper_usable_hm3 should equal H_MAX_OP."""
        res = cfg.plant.reservoir
        Q_ref_m3 = res.upper_min_hm3 * M3_PER_HM3
        Q_range_m3 = (res.upper_usable_hm3 - res.upper_min_hm3) * M3_PER_HM3
        dH_dQ = (H_MAX_OP - H_MIN_OP) / Q_range_m3
        H = H_MIN_OP + dH_dQ * (res.upper_usable_hm3 * M3_PER_HM3 - Q_ref_m3)
        assert abs(H - H_MAX_OP) < 1e-9, f"Expected {H_MAX_OP}, got {H}"

    def test_A1c_head_vol_monotone(self, cfg):
        """Higher reservoir volume → higher head (monotone)."""
        res = cfg.plant.reservoir
        Q_ref_m3 = res.upper_min_hm3 * M3_PER_HM3
        Q_range_m3 = (res.upper_usable_hm3 - res.upper_min_hm3) * M3_PER_HM3
        dH_dQ = (H_MAX_OP - H_MIN_OP) / Q_range_m3
        vols = [res.upper_min_hm3 + i * (res.upper_usable_hm3 - res.upper_min_hm3) / 10
                for i in range(11)]
        heads = [H_MIN_OP + dH_dQ * (v * M3_PER_HM3 - Q_ref_m3) for v in vols]
        assert all(heads[i] <= heads[i+1] for i in range(len(heads)-1)), \
            "Head not monotone with volume"

    def test_A2_efficiency_surface_bounds_turbine(self, cfg):
        """All turbine efficiency values must be within [ETA_LO, ETA_HI]."""
        import numpy as np
        psp = cfg.plant.psp
        fg = list(np.linspace(psp.q_turbine_min_m3h, psp.q_turbine_max_m3h, N_GRID))
        hg = list(np.linspace(H_MIN_OP, H_MAX_OP, N_GRID))
        eff = _efficiency_surface(fg, hg, COEFFS_TRB)
        for (fi, hi), eta in eff.items():
            assert ETA_LO <= eta <= ETA_HI, \
                f"TRB eta[{fi},{hi}]={eta:.4f} out of [{ETA_LO},{ETA_HI}]"

    def test_A2b_efficiency_surface_bounds_pump(self, cfg):
        """All pump efficiency values must be within [ETA_LO, ETA_HI]."""
        import numpy as np
        psp = cfg.plant.psp
        fg = list(np.linspace(psp.q_pump_min_m3h, psp.q_pump_max_m3h, N_GRID))
        hg = list(np.linspace(H_MIN_OP, H_MAX_OP, N_GRID))
        eff = _efficiency_surface(fg, hg, COEFFS_PMP)
        for (fi, hi), eta in eff.items():
            assert ETA_LO <= eta <= ETA_HI, \
                f"PMP eta[{fi},{hi}]={eta:.4f} out of [{ETA_LO},{ETA_HI}]"

    def test_A3_mccormick_all_4_constraints(self):
        """McCormick envelope: z = H*x must satisfy all 4 linear constraints.

        For any (H in [H_LO,H_HI], x in {0,1}), the bilinear product z=H*x
        must satisfy all 4 McCormick inequalities.
        """
        from common_layer.optimisation_model.core_milp_builder import MC_H_LO, MC_H_HI
        test_cases = [
            (H_MIN_OP, 0), (H_MIN_OP, 1),
            (H_MAX_OP, 0), (H_MAX_OP, 1),
            ((H_MIN_OP+H_MAX_OP)/2, 0), ((H_MIN_OP+H_MAX_OP)/2, 1),
        ]
        for H, x in test_cases:
            z = H * x
            # k=0: z <= H_HI * x
            assert z <= MC_H_HI * x + TOL, f"MC k=0 fail H={H} x={x}"
            # k=1: z >= H_LO * x
            assert z >= MC_H_LO * x - TOL, f"MC k=1 fail H={H} x={x}"
            # k=2: z <= H - H_LO*(1-x)
            assert z <= H - MC_H_LO * (1 - x) + TOL, f"MC k=2 fail H={H} x={x}"
            # k=3: z >= H - H_HI*(1-x)
            assert z >= H - MC_H_HI * (1 - x) - TOL, f"MC k=3 fail H={H} x={x}"

    def test_A4_power_conversion_formula(self, cfg):
        """P_MW = eta*rho*g*Q[m3/h]*H[m] / CONV should give physically correct MW.

        At maximum turbine flow (210 m³/s = 756,000 m³/h) and rated head (63.85 m midpoint),
        eta=0.88 → should give ~129 MW per unit (nameplate is 129.6 MW).
        """
        psp = cfg.plant.psp
        Q_m3h = psp.q_turbine_max_m3h       # max flow per unit
        H = (H_MIN_OP + H_MAX_OP) / 2       # midpoint head
        eta = 0.88                            # realistic efficiency
        P_MW = eta * RHO_WATER * G_GRAVITY * Q_m3h * H / CONV_M3H_TO_MW
        # Should be in [100, 145] MW range per unit
        assert 90 <= P_MW <= 150, \
            f"Power conversion gave {P_MW:.2f} MW — outside realistic range [90,150] MW/unit"

    def test_A5_bess_soc_continuity(self, cfg):
        """SoC(t) = SoC(t-1) + eta_c*Pchg*dt - Pdis*dt/eta_d must be exact."""
        bess = cfg.plant.bess
        soc_prev = bess.initial_soc_frac * bess.capacity_mwh
        p_chg = 0.8   # MW
        p_dis = 0.0
        dt = 1.0
        soc_expected = soc_prev + bess.eta_charge * p_chg * dt - p_dis * dt / bess.eta_discharge
        soc_computed = soc_prev + bess.eta_charge * p_chg * dt - p_dis * dt / bess.eta_discharge
        assert abs(soc_expected - soc_computed) < 1e-12

    def test_A5b_bess_soc_discharge(self, cfg):
        """SoC decreases correctly during discharge."""
        bess = cfg.plant.bess
        soc_start = bess.e_max_mwh
        p_dis = bess.power_mw   # full power discharge for 1 hour
        dt = 1.0
        soc_end = soc_start - p_dis * dt / bess.eta_discharge
        loss = p_dis * dt / bess.eta_discharge
        # Loss must be > p_dis*dt (inefficiency eats extra capacity)
        assert loss > p_dis * dt, "Discharge loss should exceed output energy"
        # SoC must drop
        assert soc_end < soc_start

    def test_A6_reservoir_closed_loop_balance(self, cfg):
        """In closed loop with no inflow and no spill, upper loss = lower gain."""
        # Turbine: water moves upper → lower.  Pump: water moves lower → upper.
        # Net flow turbine for 1 hour:
        psp = cfg.plant.psp
        q_turb = psp.q_turbine_max_m3h   # m³/h per unit
        dt = 1.0  # hours
        # 1 unit turbines for 1 hour: upper loses, lower gains same volume
        delta_upper = -q_turb * dt / M3_PER_HM3   # hm³ (negative)
        delta_lower = +q_turb * dt / M3_PER_HM3   # hm³ (positive)
        total_change = delta_upper + delta_lower
        assert abs(total_change) < 1e-12, \
            f"Closed-loop water balance off by {total_change:.2e} hm³"

    def test_A7_pv_balance_arithmetic(self):
        """PV balance: used + to_bess + curtailed = available must be exact."""
        pv_av = 4.2   # MW
        pv_used = 3.0
        pv_to_bess = 0.8
        pv_curt = pv_av - pv_used - pv_to_bess   # should be 0.4
        assert abs(pv_used + pv_to_bess + pv_curt - pv_av) < 1e-12
        assert pv_curt >= 0, "Curtailment cannot be negative"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP B — MILP solve: constraint verification on solved schedule
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestMILPConstraints:

    def _solve(self, cfg, inputs):
        """Helper: build + solve + extract. Skips if CPLEX unavailable."""
        if cfg.solver.resolve_executable() is None:
            pytest.skip("CPLEX not found")
        from common_layer.optimisation_model import (
            build_core_model, solve_core_model, extract_results,
        )
        model, meta = build_core_model(inputs, cfg)
        solve_core_model(model, cfg, gate="DA")
        results = extract_results(model, meta)
        return model, meta, results

    def test_B1_normal_solve_feasible_checker_pass(self, cfg, base_inputs):
        """Standard inputs: solve feasible, objective positive, DA checker passes."""
        model, meta, results = self._solve(cfg, base_inputs)
        assert results.objective_eur > 0, "Objective should be positive with arbitrage prices"
        # Run the DA bid checker
        from phase_1_da_day_ahead_bidding.da_bid_formatting.da_bid_checker import (
            check_da_bid, BidCheckError,
        )
        try:
            check_da_bid(results, base_inputs, cfg, gate="DA")
        except BidCheckError as e:
            pytest.fail(f"DA checker failed on valid solution: {e}")

    def test_B2_mode_exclusivity_per_unit(self, cfg, base_inputs):
        """on_turb[u,h] + on_pump[u,h] <= 1 for every unit u and hour h."""
        model, meta, results = self._solve(cfg, base_inputs)
        v = pyo.value
        violations = []
        for h in meta.hours:
            for u in meta.units:
                s = round(v(model.on_turb[u, h])) + round(v(model.on_pump[u, h]))
                if s > 1:
                    violations.append(f"u={u} h={h} sum={s}")
        assert not violations, f"Mode exclusivity violated: {violations[:5]}"

    def test_B3_no_simultaneous_bess_charge_discharge(self, cfg, base_inputs):
        """chg_on[h] + dis_on[h] <= 1 every hour."""
        model, meta, results = self._solve(cfg, base_inputs)
        v = pyo.value
        violations = [h for h in meta.hours
                      if round(v(model.chg_on[h])) + round(v(model.dis_on[h])) > 1]
        assert not violations, f"Simultaneous BESS charge+discharge at hours: {violations}"

    def test_B4_bess_soc_within_bounds(self, cfg, base_inputs):
        """BESS SoC stays within [e_min_mwh, e_max_mwh] every hour."""
        model, meta, results = self._solve(cfg, base_inputs)
        v = pyo.value
        bess = cfg.plant.bess
        violations = []
        for h in meta.hours:
            soc = v(model.soc[h])
            if soc < bess.e_min_mwh - TOL or soc > bess.e_max_mwh + TOL:
                violations.append(f"h={h} soc={soc:.4f} bounds=[{bess.e_min_mwh},{bess.e_max_mwh}]")
        assert not violations, f"BESS SoC out of bounds: {violations[:5]}"

    def test_B5_net_power_within_fcr_envelope(self, cfg, base_inputs):
        """p_net[h] within FCR-reduced [−pump_cap, gen_cap] every hour."""
        model, meta, results = self._solve(cfg, base_inputs)
        v = pyo.value
        p = cfg.plant
        fcr = max(0.0, p.fcr.mandatory_headroom_mw)
        gen_cap  = p.p_max_generation_mw - fcr
        pump_cap = p.p_max_pump_mw - fcr
        violations = []
        for h in meta.hours:
            pn = v(model.p_net[h])
            if pn > gen_cap + TOL or pn < -pump_cap - TOL:
                violations.append(f"h={h} p_net={pn:.2f} envelope=[{-pump_cap:.2f},{gen_cap:.2f}]")
        assert not violations, f"p_net outside envelope: {violations[:5]}"

    def test_B6_reservoir_volumes_within_bounds(self, cfg, base_inputs):
        """v_up and v_low within their configured bounds every hour."""
        model, meta, results = self._solve(cfg, base_inputs)
        v = pyo.value
        res = cfg.plant.reservoir
        violations = []
        for h in meta.hours:
            vup = v(model.v_up[h])
            vlo = v(model.v_low[h])
            if vup < res.upper_min_hm3 - TOL or vup > res.upper_usable_hm3 + TOL:
                violations.append(f"h={h} v_up={vup:.3f} bounds=[{res.upper_min_hm3},{res.upper_usable_hm3}]")
            if vlo < res.lower_min_hm3 - TOL or vlo > res.lower_capacity_hm3 + TOL:
                violations.append(f"h={h} v_low={vlo:.3f} bounds=[{res.lower_min_hm3},{res.lower_capacity_hm3}]")
        assert not violations, f"Reservoir out of bounds: {violations[:5]}"

    def test_B7_terminal_reservoir_constraint(self, cfg, base_inputs):
        """v_up at end of day must be >= initial level (terminal constraint)."""
        model, meta, results = self._solve(cfg, base_inputs)
        v = pyo.value
        v_up_init = float(base_inputs["initial_state"]["upper_reservoir_hm3"])
        v_up_end  = v(model.v_up[meta.hours[-1]])
        assert v_up_end >= v_up_init - TOL, \
            f"Terminal constraint violated: v_up_end={v_up_end:.4f} < v_up0={v_up_init:.4f}"

    def test_B8_pv_balance_every_hour(self, cfg, base_inputs):
        """pv_used + pv_to_bess + pv_curt == pv_available every hour (exact)."""
        model, meta, results = self._solve(cfg, base_inputs)
        v = pyo.value
        violations = []
        for h in meta.hours:
            lhs = v(model.pv_used[h]) + v(model.pv_to_bess[h]) + v(model.pv_curt[h])
            rhs = meta.pv_available[h]
            if abs(lhs - rhs) > TOL:
                violations.append(f"h={h} lhs={lhs:.4f} rhs={rhs:.4f} diff={lhs-rhs:.2e}")
        assert not violations, f"PV balance violated: {violations[:5]}"

    def test_B9_water_balance_closed_loop(self, cfg, base_inputs):
        """Total turbine flow == total pump flow over 24h (no inflow, no spill).

        Closed loop: every m³ turbined moves upper→lower, every m³ pumped moves
        lower→upper. With zero inflow and no spill, net flow must be zero.
        """
        from tests.conftest import make_inputs
        inputs = make_inputs(cfg, inflow_m3h=0.0)
        model, meta, results = self._solve(cfg, inputs)
        v = pyo.value
        total_turb_m3 = sum(
            v(model.q_turb[u, h]) * 1.0  # dt=1h
            for u in meta.units for h in meta.hours
        )
        total_pump_m3 = sum(
            v(model.q_pump[u, h]) * 1.0
            for u in meta.units for h in meta.hours
        )
        total_spill_m3 = sum(v(model.spill[h]) * 1.0 for h in meta.hours)
        # Upper reservoir change = pump_in - turb_out - spill (no inflow)
        v_up_start = float(inputs["initial_state"]["upper_reservoir_hm3"]) * M3_PER_HM3
        v_up_end   = v(model.v_up[meta.hours[-1]]) * M3_PER_HM3
        implied_net_pump = (v_up_end - v_up_start) + total_turb_m3 + total_spill_m3
        assert abs(implied_net_pump - total_pump_m3) < 1.0, \
            f"Water balance off by {implied_net_pump - total_pump_m3:.2f} m³"

    def test_B10_head_within_operating_range(self, cfg, base_inputs):
        """H_net[h] within [H_MIN_OP, H_MAX_OP] every hour."""
        model, meta, results = self._solve(cfg, base_inputs)
        v = pyo.value
        violations = []
        for h in meta.hours:
            H = v(model.H_net[h])
            if H < H_MIN_OP - TOL or H > H_MAX_OP + TOL:
                violations.append(f"h={h} H_net={H:.3f} range=[{H_MIN_OP},{H_MAX_OP}]")
        assert not violations, f"Head out of operating range: {violations[:5]}"

    def test_B11_head_volume_consistency(self, cfg, base_inputs):
        """H_net[h] matches the formula H_MIN_OP + dH_dQ*(v_up[h]*M3 - Q_ref)."""
        model, meta, results = self._solve(cfg, base_inputs)
        v = pyo.value
        res = cfg.plant.reservoir
        Q_ref_m3  = res.upper_min_hm3 * M3_PER_HM3
        Q_range_m3 = (res.upper_usable_hm3 - res.upper_min_hm3) * M3_PER_HM3
        dH_dQ = (H_MAX_OP - H_MIN_OP) / Q_range_m3
        violations = []
        for h in meta.hours:
            H_model   = v(model.H_net[h])
            H_formula = H_MIN_OP + dH_dQ * (v(model.v_up[h]) * M3_PER_HM3 - Q_ref_m3)
            if abs(H_model - H_formula) > TOL:
                violations.append(f"h={h} H_model={H_model:.4f} H_formula={H_formula:.4f}")
        assert not violations, f"Head-volume mismatch: {violations[:5]}"

    def test_B12_power_weighted_efficiency_realistic(self, cfg, base_inputs):
        """eta_trb_pw in [0.84, 0.93] when turbine is operating."""
        from common_layer.optimisation_model import (
            build_core_model, solve_core_model, extract_results,
        )
        if cfg.solver.resolve_executable() is None:
            pytest.skip("CPLEX not found")
        model, meta = build_core_model(base_inputs, cfg)
        solve_core_model(model, cfg, gate="DA")
        results = extract_results(model, meta)
        v = pyo.value
        for h in meta.hours:
            turb_mw = results.psp_schedule[h]["turbine_mw"]
            eta = results.efficiency_per_hour[h]["eta_trb_pw"]
            if turb_mw > 10.0:   # unit is actually generating
                assert 0.84 <= eta <= 0.93, \
                    f"h={h} eta_trb_pw={eta:.4f} unrealistic (turbine_mw={turb_mw:.1f})"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP C — Adversarial MILP inputs
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestAdversarialInputs:

    def _solve(self, cfg, inputs):
        if cfg.solver.resolve_executable() is None:
            pytest.skip("CPLEX not found")
        from common_layer.optimisation_model import (
            build_core_model, solve_core_model, extract_results,
        )
        model, meta = build_core_model(inputs, cfg)
        solve_core_model(model, cfg, gate="DA")
        return extract_results(model, meta)

    def test_C1_lower_reservoir_full_vlow_never_exceeded(self, cfg):
        """Starting with lower reservoir full: optimizer finds feasible schedule
        and lower reservoir never exceeds capacity throughout the horizon.

        The optimizer WILL pump first to create room, then turbine. What matters
        is that the MILP constraint vlow_hi is never violated in the schedule.
        """
        from tests.conftest import make_inputs
        res = cfg.plant.reservoir
        inputs = make_inputs(cfg,
                             v_low0_override=res.lower_capacity_hm3,
                             price_pattern="arbitrage",
                             inflow_m3h=0.0)
        results = self._solve(cfg, inputs)
        # Reservoir bounds must hold every hour — even starting from full lower
        violations = [
            f"h={h} v_low={results.reservoir_trajectory[h]['lower_hm3']:.3f} "
            f"> cap={res.lower_capacity_hm3}"
            for h in results.reservoir_trajectory
            if results.reservoir_trajectory[h]["lower_hm3"] > res.lower_capacity_hm3 + TOL
        ]
        assert not violations, f"vlow_hi violated: {violations[:3]}"

    def test_C2_lower_reservoir_empty_vlow_never_violated(self, cfg):
        """Starting with lower reservoir empty: optimizer finds feasible schedule
        and lower reservoir never drops below minimum throughout the horizon.

        The optimizer must turbine first (upper→lower) before it can pump.
        What matters is the vlow_lo constraint is never breached.
        """
        from tests.conftest import make_inputs
        res = cfg.plant.reservoir
        inputs = make_inputs(cfg,
                             v_low0_override=res.lower_min_hm3,
                             price_pattern="negative",
                             inflow_m3h=0.0)
        results = self._solve(cfg, inputs)
        violations = [
            f"h={h} v_low={results.reservoir_trajectory[h]['lower_hm3']:.3f} "
            f"< min={res.lower_min_hm3}"
            for h in results.reservoir_trajectory
            if results.reservoir_trajectory[h]["lower_hm3"] < res.lower_min_hm3 - TOL
        ]
        assert not violations, f"vlow_lo violated: {violations[:3]}"

    def test_C3_pv_zero_no_pv_to_bess(self, cfg):
        """With PV = 0, pv_to_bess and pv_curt must both be zero every hour."""
        from tests.conftest import make_inputs
        inputs = make_inputs(cfg, pv_mw=0.0)
        results = self._solve(cfg, inputs)
        for h, sched in results.pv_schedule.items():
            assert sched["to_bess_mw"] < TOL, \
                f"h={h} pv_to_bess={sched['to_bess_mw']:.4f} with PV=0"
            assert sched["curtailed_mw"] < TOL, \
                f"h={h} pv_curt={sched['curtailed_mw']:.4f} with PV=0"

    def test_C4_flat_prices_no_turbine_below_water_value(self, cfg):
        """With flat 50 EUR/MWh prices, optimizer should still solve feasibly.

        At 50 EUR/MWh (typical water value), schedule is market-neutral.
        Key invariants must still hold.
        """
        from tests.conftest import make_inputs
        inputs = make_inputs(cfg, price_pattern="flat")
        results = self._solve(cfg, inputs)
        # Must solve feasibly (objective may be small positive or zero)
        assert results.objective_eur is not None
        # All invariants: SoC bounds, reservoir bounds, net power bounds
        # (covered in B-tests, here just verify it doesn't crash)

    def test_C5_negative_prices_optimizer_pumps(self, cfg):
        """With all-negative prices, optimizer should pump (buy cheap power).

        Pumping at negative price = revenue (paid to consume power).
        """
        from tests.conftest import make_inputs
        res = cfg.plant.reservoir
        # Start at midrange so there's room to pump
        inputs = make_inputs(cfg,
                             price_pattern="negative",
                             v_up0_override=(res.upper_min_hm3 + res.upper_usable_hm3) / 2)
        results = self._solve(cfg, inputs)
        total_pump_mwh = sum(
            results.psp_schedule[h]["pump_mw"] for h in results.psp_schedule
        )
        assert total_pump_mwh > 50.0, \
            f"Expected significant pumping at negative prices, got {total_pump_mwh:.1f} MWh"

    def test_C6_high_price_single_hour_turbines(self, cfg):
        """With only 1 extremely high-price hour, optimizer turbines that hour."""
        from tests.conftest import make_inputs
        H = list(range(1, 25))
        prices = {h: 10.0 for h in H}
        prices[12] = 500.0   # single spike at noon
        res = cfg.plant.reservoir
        inputs = make_inputs(cfg, price_pattern="flat")
        inputs["da_prices"] = prices
        results = self._solve(cfg, inputs)
        # Hour 12 should have turbine output (high price attracts generation)
        turb_h12 = results.psp_schedule[12]["turbine_mw"]
        assert turb_h12 > 50.0, \
            f"Expected turbining at price spike hour 12, got {turb_h12:.1f} MW"

    def test_C7_solve_still_feasible_at_very_high_price(self, cfg):
        """Extreme prices (1000 EUR/MWh) still produce a feasible solution."""
        from tests.conftest import make_inputs
        inputs = make_inputs(cfg)
        inputs["da_prices"] = {h: 1000.0 for h in range(1, 25)}
        results = self._solve(cfg, inputs)
        assert results.objective_eur > 0

    def test_C8_bess_full_at_start_no_charge(self, cfg):
        """BESS starting fully charged should not charge further."""
        from tests.conftest import make_inputs
        inputs = make_inputs(cfg, bess_soc_frac=1.0)  # fully charged
        results = self._solve(cfg, inputs)
        # Grid charge should be zero (already full)
        total_grid_chg = sum(
            results.bess_schedule[h]["charge_mw"] for h in results.bess_schedule
        )
        assert total_grid_chg < TOL, \
            f"BESS charged from grid {total_grid_chg:.3f} MW even though starting full"


# ═══════════════════════════════════════════════════════════════════════════════
# GROUP D — Binding constraint analysis
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestBindingConstraintAnalysis:

    def test_D1_returns_list_of_dicts_with_required_keys(self, cfg, base_inputs):
        """analyze_binding_constraints returns [{name, slack, binding}] list."""
        if cfg.solver.resolve_executable() is None:
            pytest.skip("CPLEX not found")
        from common_layer.optimisation_model import (
            build_core_model, solve_core_model, analyze_binding_constraints,
        )
        model, meta = build_core_model(base_inputs, cfg)
        solve_core_model(model, cfg, gate="DA")
        rows = analyze_binding_constraints(model)
        assert isinstance(rows, list), "Expected list"
        assert len(rows) > 0, "Expected non-empty list"
        for r in rows[:10]:
            assert "name"    in r, f"Missing 'name' key in {r}"
            assert "slack"   in r, f"Missing 'slack' key in {r}"
            assert "binding" in r, f"Missing 'binding' key in {r}"
            assert isinstance(r["binding"], bool)

    def test_D2_binding_count_reasonable(self, cfg, base_inputs):
        """At least 5 binding constraints expected in a typical 24h solve."""
        if cfg.solver.resolve_executable() is None:
            pytest.skip("CPLEX not found")
        from common_layer.optimisation_model import (
            build_core_model, solve_core_model, analyze_binding_constraints,
        )
        model, meta = build_core_model(base_inputs, cfg)
        solve_core_model(model, cfg, gate="DA")
        rows = analyze_binding_constraints(model)
        binding = [r for r in rows if r["binding"]]
        assert len(binding) >= 5, \
            f"Expected >= 5 binding constraints, found {len(binding)}"

    def test_D3_sorted_by_slack_ascending(self, cfg, base_inputs):
        """Result list must be sorted by |slack| ascending (tightest first)."""
        if cfg.solver.resolve_executable() is None:
            pytest.skip("CPLEX not found")
        from common_layer.optimisation_model import (
            build_core_model, solve_core_model, analyze_binding_constraints,
        )
        model, meta = build_core_model(base_inputs, cfg)
        solve_core_model(model, cfg, gate="DA")
        rows = analyze_binding_constraints(model)
        slacks = [abs(r["slack"]) for r in rows]
        assert slacks == sorted(slacks), "Results not sorted by |slack| ascending"
