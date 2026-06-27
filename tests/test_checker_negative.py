"""
test_checker_negative.py — negative-path bid checker tests.

Verify that check_da_bid RAISES BidCheckError with the expected PR/INV tag when a
deliberate violation is injected into an otherwise-valid GateResults object.

All tests are pure (no solver): we build a minimal valid schedule by hand, verify
it passes clean, then corrupt one field per test and assert the raise.

Golden schedule design: ALL-IDLE.
  * Every PSP unit turbine=0, pump=0 (unit off — PR-2 satisfied trivially).
  * BESS charge=0, discharge=0.
  * PV used=0, available=5.0 MW.
  * Net = 0 MW; bid volume = 0 MWh, price = 50 EUR/MWh.
  * No reservoir flows → trajectory holds within bounds.

Using all-idle avoids the operational complexity of min-stable-load vs
reservoir capacity constraints in the test setup itself.
"""
from __future__ import annotations

import copy
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config
from common_layer.optimisation_model.core_milp_solver import GateResults
from phase_1_da_day_ahead_bidding.da_bid_formatting.da_bid_checker import (
    check_da_bid, BidCheckError,
)


H = list(range(1, 25))
DT = 1.0


# ---------------------------------------------------------------------------
# Golden builder — all-idle
# ---------------------------------------------------------------------------

def _golden(cfg) -> tuple[GateResults, dict]:
    """Minimal all-idle valid schedule: every unit off, BESS idle, PV unused, net=0."""
    plant = cfg.plant
    n_units = plant.psp.n_units

    psp_sched = {
        h: {
            "units_turbine": {u: 0.0 for u in range(n_units)},
            "units_pump":    {u: 0.0 for u in range(n_units)},
            "total_turbine_mw": 0.0,
            "total_pump_mw": 0.0,
        }
        for h in H
    }
    bess_sched = {h: {"charge_mw": 0.0, "discharge_mw": 0.0} for h in H}
    pv_sched   = {h: {"used_mw": 0.0, "available_mw": 5.0}   for h in H}
    res_traj   = {h: {"spill_m3h": 0.0,
                       "upper_hm3": plant.reservoir.upper_initial_hm3,
                       "lower_hm3": plant.reservoir.lower_initial_hm3}
                  for h in H}
    da_bids = {h: {"volume_mwh": 0.0, "price_eur_mwh": 50.0} for h in H}
    net_pos = {h: 0.0 for h in H}
    eff_ph  = {h: {"eta_trb_pw": 0.0, "eta_pmp_pw": 0.0} for h in H}

    results = GateResults(
        da_bids=da_bids,
        net_position_mw=net_pos,
        psp_schedule=psp_sched,
        bess_schedule=bess_sched,
        pv_schedule=pv_sched,
        reservoir_trajectory=res_traj,
        efficiency_per_hour=eff_ph,
        energy_revenue_eur=0.0,
        objective_eur=0.0,
    )
    inputs = {
        "hours": H,
        "dt_h": DT,
        "delivery_date": "2026-06-24",
        "da_prices": {h: 50.0 for h in H},
        "pv_available_mw": {h: 5.0 for h in H},
        "inflow_m3h": {h: 0.0 for h in H},
        "initial_state": {
            "upper_reservoir_hm3": plant.reservoir.upper_initial_hm3,
            "lower_reservoir_hm3": plant.reservoir.lower_initial_hm3,
            "bess_soc_frac": plant.bess.initial_soc_frac,
        },
    }
    return results, inputs


# ---------------------------------------------------------------------------
# Shared cfg fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cfg():
    return load_config()


# ---------------------------------------------------------------------------
# N0: golden must pass clean
# ---------------------------------------------------------------------------

def test_N0_golden_passes(cfg):
    """All-idle golden schedule must pass checker with zero violations."""
    results, inputs = _golden(cfg)
    v = check_da_bid(results, inputs, cfg, gate="DA")
    assert v == []


# ---------------------------------------------------------------------------
# N1: PR-1 — simultaneous turbine + pump on same unit
# ---------------------------------------------------------------------------

def test_N1_pr1_mode_exclusivity_raises(cfg):
    """PR-1 violation: unit 0 turbines AND pumps in hour 1."""
    plant = cfg.plant
    min_load = plant.psp.p_turbine_min_mw   # e.g. 57 MW
    min_pump = plant.psp.p_pump_max_mw * 0.4  # safe pump level above min

    results, inputs = _golden(cfg)
    r = copy.deepcopy(results)

    # Set both turbine and pump on unit 0 in hour 1.
    r.psp_schedule[1]["units_turbine"][0] = min_load
    r.psp_schedule[1]["units_pump"][0]    = min_pump

    # Keep INV-1 consistent so it doesn't fire before PR-1.
    psp_net_h1 = (r.psp_schedule[1]["units_turbine"][0]
                  - r.psp_schedule[1]["units_pump"][0])
    r.net_position_mw[1] = psp_net_h1
    r.da_bids[1]["volume_mwh"] = psp_net_h1 * DT

    with pytest.raises(BidCheckError) as exc:
        check_da_bid(r, inputs, cfg, gate="DA")
    err = str(exc.value)
    assert "PR-1" in err or "PR-2" in err


# ---------------------------------------------------------------------------
# N2: PR-7 — BESS SOC below minimum from over-discharge
# ---------------------------------------------------------------------------

def test_N2_pr7_soc_below_min_raises(cfg):
    """PR-7 violation: BESS discharged every hour at rated power; SOC < e_min after h1."""
    plant = cfg.plant
    bess = plant.bess
    power_mw = bess.power_mw   # 1 MW

    results, inputs = _golden(cfg)
    r = copy.deepcopy(results)

    # Discharge at rated power every hour; keep INV-1 consistent (BESS discharges to grid).
    for h in H:
        r.bess_schedule[h]["discharge_mw"] = power_mw
        r.net_position_mw[h] = power_mw
        r.da_bids[h]["volume_mwh"] = power_mw * DT

    # SOC0 = initial_soc_frac × capacity = 0.5 × 2.0 = 1.0 MWh.
    # After h1: E = 1.0 - power_mw / eta_d × 1h → below e_min = 0.2 MWh → PR-7 fires.
    with pytest.raises(BidCheckError) as exc:
        check_da_bid(r, inputs, cfg, gate="DA")
    assert "PR-7" in str(exc.value)


# ---------------------------------------------------------------------------
# N3: PR-4 — net power above market generation cap
# ---------------------------------------------------------------------------

def test_N3_pr4_net_above_gen_cap_raises(cfg):
    """PR-4 violation: net_position_mw > max_generation_mw in hour 5."""
    results, inputs = _golden(cfg)
    r = copy.deepcopy(results)
    gen_cap = cfg.market.bid_limits.max_generation_mw
    r.net_position_mw[5] = gen_cap + 10.0
    r.da_bids[5]["volume_mwh"] = (gen_cap + 10.0) * DT
    with pytest.raises(BidCheckError) as exc:
        check_da_bid(r, inputs, cfg, gate="DA")
    assert "PR-4" in str(exc.value)


# ---------------------------------------------------------------------------
# N4: INV-1 — energy balance mismatch (net != PSP + PV + BESS)
# ---------------------------------------------------------------------------

def test_N4_inv1_energy_balance_mismatch_raises(cfg):
    """INV-1 violation: net_position_mw inflated without touching PSP/BESS/PV in h3."""
    results, inputs = _golden(cfg)
    r = copy.deepcopy(results)
    # Net says 50 MW but PSP+PV+BESS = 0 MW.
    r.net_position_mw[3] = 50.0
    r.da_bids[3]["volume_mwh"] = 50.0 * DT
    with pytest.raises(BidCheckError) as exc:
        check_da_bid(r, inputs, cfg, gate="DA")
    assert "INV-1" in str(exc.value)


# ---------------------------------------------------------------------------
# N5: PR-5 — upper reservoir drained below minimum
# ---------------------------------------------------------------------------

def test_N5_pr5_reservoir_upper_min_raises(cfg):
    """PR-5 violation: turbine 1 unit at min stable load from near-minimum upper reservoir."""
    from common_layer.physical_plant_models import PSPModel

    plant = cfg.plant
    psp_model = PSPModel(plant.psp)
    min_load = plant.psp.p_turbine_min_mw
    n_units = plant.psp.n_units

    # Turbine flow per unit at min stable load.
    turb_flow = psp_model.turbine_flow_m3h(min_load)  # m³/h

    # Build a turbining-only schedule: 1 unit on for all hours.
    psp_sched = {}
    for h in H:
        psp_sched[h] = {
            "units_turbine": {0: min_load, **{u: 0.0 for u in range(1, n_units)}},
            "units_pump":    {u: 0.0 for u in range(n_units)},
            "total_turbine_mw": min_load,
            "total_pump_mw": 0.0,
        }

    bess_sched = {h: {"charge_mw": 0.0, "discharge_mw": 0.0} for h in H}
    pv_sched   = {h: {"used_mw": 0.0, "available_mw": 5.0}   for h in H}
    res_traj   = {h: {"spill_m3h": 0.0,
                       "upper_hm3": plant.reservoir.upper_initial_hm3,
                       "lower_hm3": plant.reservoir.lower_initial_hm3}
                  for h in H}
    da_bids = {h: {"volume_mwh": min_load * DT, "price_eur_mwh": 50.0} for h in H}
    net_pos = {h: min_load for h in H}

    results = GateResults(
        da_bids=da_bids,
        net_position_mw=net_pos,
        psp_schedule=psp_sched,
        bess_schedule=bess_sched,
        pv_schedule=pv_sched,
        reservoir_trajectory=res_traj,
        efficiency_per_hour={h: {} for h in H},
        energy_revenue_eur=0.0,
        objective_eur=0.0,
    )

    # Set upper reservoir to just above minimum so turbining for 1 hour drains it below.
    # turb_flow × 1h / 1e6 hm³ >> 0.01 hm³ margin → PR-5 fires after hour 1.
    inputs = {
        "hours": H,
        "dt_h": DT,
        "delivery_date": "2026-06-24",
        "da_prices": {h: 50.0 for h in H},
        "pv_available_mw": {h: 5.0 for h in H},
        "inflow_m3h": {h: 0.0 for h in H},
        "initial_state": {
            "upper_reservoir_hm3": plant.reservoir.upper_min_hm3 + 0.01,
            "lower_reservoir_hm3": plant.reservoir.lower_min_hm3,
            "bess_soc_frac": plant.bess.initial_soc_frac,
        },
    }

    with pytest.raises(BidCheckError) as exc:
        check_da_bid(results, inputs, cfg, gate="DA")
    err = str(exc.value)
    assert "PR-5" in err or "PR-6" in err


# ---------------------------------------------------------------------------
# N6: PR-10 — PV used > available
# ---------------------------------------------------------------------------

def test_N6_pr10_pv_exceeds_available_raises(cfg):
    """PR-10 violation: used_mw > available_mw in hour 13."""
    results, inputs = _golden(cfg)
    r = copy.deepcopy(results)
    r.pv_schedule[13]["used_mw"] = 10.0     # available_mw = 5.0
    # Keep INV-1 consistent.
    r.net_position_mw[13] = 10.0
    r.da_bids[13]["volume_mwh"] = 10.0 * DT
    with pytest.raises(BidCheckError) as exc:
        check_da_bid(r, inputs, cfg, gate="DA")
    assert "PR-10" in str(exc.value)


# ---------------------------------------------------------------------------
# N7: bid price outside OMIE bounds
# ---------------------------------------------------------------------------

def test_N7_bid_price_out_of_bounds_raises(cfg):
    """Bid price check: price above OMIE max must raise BidCheckError."""
    results, inputs = _golden(cfg)
    r = copy.deepcopy(results)
    max_price = cfg.market.bid_limits.price_max_eur_mwh
    r.da_bids[10]["price_eur_mwh"] = max_price + 500.0
    with pytest.raises(BidCheckError) as exc:
        check_da_bid(r, inputs, cfg, gate="DA")
    err_lower = str(exc.value).lower()
    assert "omie" in err_lower or "price" in err_lower


# ---------------------------------------------------------------------------
# N8: PR-8 — simultaneous BESS charge and discharge
# ---------------------------------------------------------------------------

def test_N8_pr8_bess_simultaneous_charge_discharge_raises(cfg):
    """PR-8 violation: BESS charges AND discharges in the same hour."""
    results, inputs = _golden(cfg)
    r = copy.deepcopy(results)
    r.bess_schedule[7]["charge_mw"]    = 0.5
    r.bess_schedule[7]["discharge_mw"] = 0.5
    # net = discharge - charge = 0 → INV-1 still ok (net stays 0).
    with pytest.raises(BidCheckError) as exc:
        check_da_bid(r, inputs, cfg, gate="DA")
    assert "PR-8" in str(exc.value)
