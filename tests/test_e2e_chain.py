"""
test_e2e_chain.py — end-to-end DA → aFRR chain integration test.

End-to-end DA → aFRR chain test:
  1. Solve MILP (DA gate).
  2. Extract net position.
  3. Build aFRR reserve offers from leftover headroom.
  4. Run reserve checker — must pass clean (no violations).

This exercises the full sequential energy-first-then-reserve pathway in a single
integration test, validating that the three stages are mutually consistent.
"""
from __future__ import annotations

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.conftest import make_inputs


@pytest.mark.integration
def test_E1_da_to_afrr_chain_passes_clean(cfg, cplex_available):
    """Full DA→aFRR chain: solve → extract → build offers → check — no violations."""
    if not cplex_available:
        pytest.skip("CPLEX not found")

    from common_layer.optimisation_model import (
        build_core_model, solve_core_model, extract_results,
    )
    from common_layer.optimisation_model.reserve_offer_builder import (
        build_reserve_offers, check_reserve_offers,
    )

    inputs = make_inputs(cfg, price_pattern="arbitrage")
    m, meta = build_core_model(inputs, cfg)
    try:
        solve_core_model(m, meta=None, cfg=cfg, gate="DA")
    except TypeError:
        solve_core_model(m, cfg, gate="DA")
    results = extract_results(m, meta)

    net = results.net_position_mw   # {h: MW}
    pv  = inputs["pv_available_mw"]

    afrr_fat = cfg.plant.psp.afrr_fat_min if hasattr(cfg.plant.psp, "afrr_fat_min") else 5.0

    # Uniform capacity prices at REN cap (250 EUR/MW)
    cap_prices_up = {h: 250.0 for h in net}
    cap_prices_dn = {h: 100.0 for h in net}

    afrr_cfg = cfg.market.afrr if hasattr(cfg.market, "afrr") else None
    max_up = (afrr_cfg.max_offer_up_mw if afrr_cfg else cfg.plant.p_max_generation_mw)
    max_dn = (afrr_cfg.max_offer_dn_mw if afrr_cfg else cfg.plant.p_max_pump_mw)

    offers = build_reserve_offers(
        product="aFRR",
        committed_net=net,
        cap_prices_up=cap_prices_up,
        cap_prices_dn=cap_prices_dn,
        cfg=cfg,
        fat_min=afrr_fat,
        max_up_mw=max_up,
        max_dn_mw=max_dn,
        pv_available_mw=pv,
    )

    # Must pass the reserve checker with zero violations.
    violations = check_reserve_offers(
        offers=offers,
        committed_net=net,
        cfg=cfg,
        fat_min=afrr_fat,
        product="aFRR",
        cap_price_max=250.0,
        pv_available_mw=pv,
    )
    assert violations == [], (
        f"Reserve checker found violations after DA→aFRR chain:\n" +
        "\n".join(f"  {v}" for v in violations)
    )


@pytest.mark.integration
def test_E2_reserve_offers_never_exceed_headroom(cfg, cplex_available):
    """Per-hour: offer_up + energy_net <= gen_cap; energy_net - offer_dn >= -pump_cap."""
    if not cplex_available:
        pytest.skip("CPLEX not found")

    from common_layer.optimisation_model import (
        build_core_model, solve_core_model, extract_results,
    )
    from common_layer.optimisation_model.reserve_offer_builder import (
        build_reserve_offers, _envelope,
    )

    inputs = make_inputs(cfg, price_pattern="arbitrage")
    m, meta = build_core_model(inputs, cfg)
    try:
        solve_core_model(m, meta=None, cfg=cfg, gate="DA")
    except TypeError:
        solve_core_model(m, cfg, gate="DA")
    results = extract_results(m, meta)

    net = results.net_position_mw
    pv  = inputs["pv_available_mw"]
    afrr_fat = 5.0

    offers = build_reserve_offers(
        product="aFRR",
        committed_net=net,
        cap_prices_up={h: 250.0 for h in net},
        cap_prices_dn={h: 100.0 for h in net},
        cfg=cfg,
        fat_min=afrr_fat,
        max_up_mw=cfg.plant.p_max_generation_mw,
        max_dn_mw=cfg.plant.p_max_pump_mw,
        pv_available_mw=pv,
    )

    p_gen_cap, p_pump_cap = _envelope(cfg)
    EPS = 1e-4

    violations = []
    for h, off in offers.items():
        n = net[h]
        if n + off.up_mw > p_gen_cap + EPS:
            violations.append(f"H{h}: energy {n:.2f} + up {off.up_mw:.2f} > gen_cap {p_gen_cap:.2f}")
        if n - off.dn_mw < -p_pump_cap - EPS:
            violations.append(f"H{h}: energy {n:.2f} - dn {off.dn_mw:.2f} < -pump_cap {-p_pump_cap:.2f}")

    assert not violations, "Headroom envelope violated:\n" + "\n".join(violations)
