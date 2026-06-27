"""
conftest.py — shared pytest fixtures for the Alqueva pipeline test suite.

Fixtures are layered:
    cfg          — AppConfig loaded from config/
    base_inputs  — standard 24-hour MILP inputs (midrange reservoir, typical prices)
    solved       — (model, meta, results) for the base case — cached per session
    cplex_skip   — skips a test if CPLEX executable not found

Mark slow integration tests with @pytest.mark.integration so they can be
excluded from fast CI with:  pytest -m "not integration"
"""
from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


# ── markers ──────────────────────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires CPLEX and runs the full MILP solver (~1–3 s each)"
    )


# ── config fixture ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def cfg():
    from common_layer.configuration import load_config
    return load_config()


# ── helper: build a clean 24-hour inputs dict ─────────────────────────────────

def make_inputs(cfg, *,
                price_pattern="arbitrage",
                v_up0_override=None,
                v_low0_override=None,
                bess_soc_frac=0.5,
                pv_mw=None,
                inflow_m3h=0.0,
                da_prices_override=None):
    """Build a 24-hour MILP inputs dict for testing.

    price_pattern:
        "arbitrage"  — high at night/evening, low midday  (default)
        "flat"       — 50 EUR/MWh flat
        "zero"       — 0 EUR/MWh (tests water-value-only decisions)
        "negative"   — -20 EUR/MWh all hours (forces pumping)
    """
    res = cfg.plant.reservoir

    H = list(range(1, 25))
    # Typical DA price: high at 07-09 and 18-22, low at 13-16.
    _arb = {
        1: 55, 2: 52, 3: 50, 4: 48, 5: 46, 6: 44,
        7: 65, 8: 80, 9: 78, 10: 70, 11: 60, 12: 45,
        13: 35, 14: 30, 15: 28, 16: 32, 17: 40, 18: 58,
        19: 75, 20: 82, 21: 85, 22: 80, 23: 72, 24: 60,
    }
    if price_pattern == "arbitrage":
        prices = _arb
    elif price_pattern == "flat":
        prices = {h: 50.0 for h in H}
    elif price_pattern == "zero":
        prices = {h: 0.0 for h in H}
    elif price_pattern == "negative":
        prices = {h: -20.0 for h in H}
    else:
        raise ValueError(f"Unknown price_pattern: {price_pattern}")

    # PV: zero at night, peaks midday at 80% of peak to stay safely below
    # what the solar model computes (avoids PR-10 checker violations in tests).
    if pv_mw is None:
        _pv = {h: max(0.0, cfg.plant.pv.peak_capacity_mw * 0.80
                      * max(0.0, 1 - ((h - 13) / 6) ** 2))
               for h in H}
    else:
        _pv = {h: float(pv_mw) for h in H}

    v_up0 = v_up0_override if v_up0_override is not None else res.upper_initial_hm3
    v_low0 = v_low0_override if v_low0_override is not None else res.lower_initial_hm3

    return {
        "hours": H,
        "dt_h": 1.0,
        "da_prices": prices,
        "pv_available_mw": _pv,
        "inflow_m3h": {h: float(inflow_m3h) for h in H},
        "initial_state": {
            "upper_reservoir_hm3": v_up0,
            "lower_reservoir_hm3": v_low0,
            "bess_soc_frac": bess_soc_frac,
        },
    }


@pytest.fixture(scope="session")
def base_inputs(cfg):
    return make_inputs(cfg)


# ── solved-model fixture (cached — runs CPLEX once per session) ───────────────

@pytest.fixture(scope="session")
def solved(cfg, base_inputs):
    """Build and solve the base-case MILP. Skips entire session if CPLEX absent."""
    from common_layer.optimisation_model import (
        build_core_model, solve_core_model, extract_results, SolveError,
    )
    exe = cfg.solver.resolve_executable()
    if exe is None:
        pytest.skip("CPLEX not found — skipping all integration tests")

    model, meta = build_core_model(base_inputs, cfg)
    try:
        solve_core_model(model, meta=None, cfg=cfg, gate="DA")
    except TypeError:
        solve_core_model(model, cfg, gate="DA")
    results = extract_results(model, meta)
    return model, meta, results


# ── skip helper ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def cplex_available(cfg):
    return cfg.solver.resolve_executable() is not None
