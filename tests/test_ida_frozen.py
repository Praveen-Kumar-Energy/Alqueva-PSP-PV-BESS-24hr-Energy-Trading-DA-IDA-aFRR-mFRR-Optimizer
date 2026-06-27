"""
test_ida_frozen.py — IDA frozen-hour mechanism tests.

Verify that the IDA frozen-hour mechanism works correctly:
  * Hours passed in fixed_net_position are locked to their committed value.
  * Hours outside fixed_net_position are free to re-optimise.
  * The delta bid (new_net - committed) is zero for frozen hours.

All tests are integration (require CPLEX).  Tag: @pytest.mark.integration.
"""
from __future__ import annotations

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import make_inputs

TOL = 1e-4   # MW — solver numerical tolerance


# ---------------------------------------------------------------------------
# I1: frozen hours are exact
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_I1_frozen_hours_exact(cfg, cplex_available):
    """Hours 1-12 in fixed_net_position must appear as p_net == value post-solve."""
    if not cplex_available:
        pytest.skip("CPLEX not found")

    from common_layer.optimisation_model import (
        build_core_model, solve_core_model, extract_results,
    )

    inputs = make_inputs(cfg, price_pattern="arbitrage")

    # First solve: DA (no frozen hours) — get baseline net position.
    m0, meta0 = build_core_model(inputs, cfg)
    try:
        solve_core_model(m0, meta=None, cfg=cfg, gate="DA")
    except TypeError:
        solve_core_model(m0, cfg, gate="DA")
    r0 = extract_results(m0, meta0)

    # Freeze hours 1-12 to their DA values.
    frozen = {h: r0.net_position_mw[h] for h in range(1, 13)}

    # IDA re-solve with modified prices (signal to change hours 13-24).
    ida_inputs = dict(inputs)
    ida_inputs["da_prices"] = {h: (90.0 if h >= 13 else inputs["da_prices"][h])
                                for h in range(1, 25)}

    m1, meta1 = build_core_model(ida_inputs, cfg, fixed_net_position=frozen)
    try:
        solve_core_model(m1, meta=None, cfg=cfg, gate="IDA1")
    except TypeError:
        solve_core_model(m1, cfg, gate="IDA1")
    r1 = extract_results(m1, meta1)

    for h in range(1, 13):
        assert abs(r1.net_position_mw[h] - frozen[h]) < TOL, (
            f"Frozen hour {h}: expected {frozen[h]:.4f} MW, "
            f"got {r1.net_position_mw[h]:.4f} MW"
        )


# ---------------------------------------------------------------------------
# I2: unfrozen hours are free to re-optimise
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_I2_unfrozen_hours_can_change(cfg, cplex_available):
    """Hours 13-24 must remain free; with a big price spike they should change."""
    if not cplex_available:
        pytest.skip("CPLEX not found")

    from common_layer.optimisation_model import (
        build_core_model, solve_core_model, extract_results,
    )

    inputs = make_inputs(cfg, price_pattern="arbitrage")

    m0, meta0 = build_core_model(inputs, cfg)
    try:
        solve_core_model(m0, meta=None, cfg=cfg, gate="DA")
    except TypeError:
        solve_core_model(m0, cfg, gate="DA")
    r0 = extract_results(m0, meta0)

    frozen = {h: r0.net_position_mw[h] for h in range(1, 13)}

    # Price spike in hours 20-24 should force maximum turbining there.
    ida_inputs = dict(inputs)
    ida_inputs["da_prices"] = {
        h: (200.0 if h >= 20 else inputs["da_prices"][h]) for h in range(1, 25)
    }

    m1, meta1 = build_core_model(ida_inputs, cfg, fixed_net_position=frozen)
    try:
        solve_core_model(m1, meta=None, cfg=cfg, gate="IDA1")
    except TypeError:
        solve_core_model(m1, cfg, gate="IDA1")
    r1 = extract_results(m1, meta1)

    # At 200 EUR/MWh, the plant should be turbining in hours 20-24.
    # Check that at least one unfrozen hour has positive net (turbining).
    unfrozen_net = [r1.net_position_mw[h] for h in range(13, 25)]
    assert any(n > 1.0 for n in unfrozen_net), (
        "Expected some turbining in unfrozen hours 13-24 under 200 EUR/MWh price spike; "
        f"got net positions {unfrozen_net}"
    )


# ---------------------------------------------------------------------------
# I3: delta bid = new_net - committed is zero for frozen hours
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_I3_delta_bid_zero_for_frozen_hours(cfg, cplex_available):
    """IDA delta bids for frozen hours must be exactly zero (no re-trading)."""
    if not cplex_available:
        pytest.skip("CPLEX not found")

    from common_layer.optimisation_model import (
        build_core_model, solve_core_model, extract_results,
    )

    inputs = make_inputs(cfg, price_pattern="flat")

    m0, meta0 = build_core_model(inputs, cfg)
    try:
        solve_core_model(m0, meta=None, cfg=cfg, gate="DA")
    except TypeError:
        solve_core_model(m0, cfg, gate="DA")
    r0 = extract_results(m0, meta0)

    frozen = {h: r0.net_position_mw[h] for h in range(1, 25)}  # freeze all hours

    # Re-solve with same prices — optimizer has nothing to change.
    m1, meta1 = build_core_model(inputs, cfg, fixed_net_position=frozen)
    try:
        solve_core_model(m1, meta=None, cfg=cfg, gate="IDA1")
    except TypeError:
        solve_core_model(m1, cfg, gate="IDA1")
    r1 = extract_results(m1, meta1)

    for h in range(1, 25):
        delta = r1.net_position_mw[h] - frozen[h]
        assert abs(delta) < TOL, (
            f"Hour {h}: frozen to {frozen[h]:.4f} MW but re-solve gives "
            f"{r1.net_position_mw[h]:.4f} MW (delta={delta:.6f})"
        )
