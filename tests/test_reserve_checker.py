"""
test_reserve_checker.py — reserve offer builder and checker correctness tests.

These tests require NO solver — they test the pure headroom sizing logic and
the checker that validates every offer before market submission.

Structure
---------
Group E  FAT deliverability formula (pure arithmetic)
    E1  Generation mode: fat_deliverable = ramp*FAT + BESS
    E2  Pump mode, short FAT: up limited to pump-ramp-to-zero + BESS
    E3  Pump mode, long FAT: mode switch allowed → full ramp + BESS
    E4  PV-flag gating: no PV → BESS excluded from upward FAT
    E5  PV-flag gating: PV available → BESS included in upward FAT
    E6  Down direction always full ramp regardless of mode

Group F  Reserve offer builder (build_reserve_offers)
    F1  Offer_up <= gen_cap − energy (no MW sold twice, PR-11)
    F2  Offer_dn <= energy + pump_cap (no MW sold twice, PR-11 down)
    F3  Offer_up <= fat_deliverable (PR-12)
    F4  Offer_dn <= fat_deliverable_dn (PR-12 down)
    F5  headroom_fraction < 1 reduces offer proportionally
    F6  reserved_up correctly reduces available headroom for secondary product
    F7  Zero headroom hours produce zero offers

Group G  Reserve checker (check_reserve_offers)
    G1  Valid offer passes clean (returns empty list)
    G2  PR-11 up violation detected: offer_up + energy > gen_cap
    G3  PR-11 down violation detected: energy − offer_dn < −pump_cap
    G4  PR-12 FAT violation detected: offer_up > fat_deliverable
    G5  Combined aFRR + mFRR across products: prior reserved_up reduces headroom
    G6  Negative reserve offer detected
    G7  PV-gated BESS: offer sized without BESS when PV=0 still passes checker

Group H  Combined activation headroom
    H1  check_combined_activation_headroom concept: logic verified on mock data
"""
from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from common_layer.optimisation_model.reserve_offer_builder import (
    fat_deliverable_mw, fat_deliverable_dn_mw,
    build_reserve_offers, check_reserve_offers,
    ReserveOffer, ReserveCheckError,
    _AFRR_FAT_MODE_SWITCH_MIN, _MFRR_FAT_MODE_SWITCH_MIN, _MIN_SAFE_MODE_SWITCH_MIN,
)


# ── Group E — FAT deliverability formula ────────────────────────────────────

class TestFATDeliverability:

    def test_E1_generation_mode_full_ramp(self, cfg):
        """Generation mode: fat_deliverable = ramp_cap*FAT + BESS."""
        fat = _AFRR_FAT_MODE_SWITCH_MIN  # 5 min
        expected = cfg.plant.psp.total_ramp_mw_per_min * fat + cfg.plant.bess.power_mw
        got = fat_deliverable_mw(cfg, fat, current_net_mw=100.0, pv_available_mw=1.0)
        assert abs(got - expected) < 1e-6, f"Expected {expected:.2f}, got {got:.2f}"

    def test_E2_pump_mode_short_fat_limited(self, cfg):
        """Pump mode, FAT < _MIN_SAFE_MODE_SWITCH_MIN: up limited to pump→0 + BESS."""
        fat = _AFRR_FAT_MODE_SWITCH_MIN   # 5 min < 8 min threshold
        pump_mw = -200.0   # currently pumping at 200 MW
        got = fat_deliverable_mw(cfg, fat, current_net_mw=pump_mw, pv_available_mw=1.0)
        ramp_cap = cfg.plant.psp.total_ramp_mw_per_min * fat
        expected = min(abs(pump_mw), ramp_cap) + cfg.plant.bess.power_mw
        assert abs(got - expected) < 1e-6, f"Expected {expected:.2f}, got {got:.2f}"

    def test_E3_pump_mode_long_fat_allows_switch(self, cfg):
        """Pump mode, FAT >= _MIN_SAFE_MODE_SWITCH_MIN: full ramp allowed (mode switch safe)."""
        fat = _MFRR_FAT_MODE_SWITCH_MIN   # 12.5 min > 8 min threshold
        got = fat_deliverable_mw(cfg, fat, current_net_mw=-200.0, pv_available_mw=1.0)
        expected = cfg.plant.psp.total_ramp_mw_per_min * fat + cfg.plant.bess.power_mw
        assert abs(got - expected) < 1e-6, f"Expected {expected:.2f}, got {got:.2f}"

    def test_E4_pv_gating_no_pv_excludes_bess(self, cfg):
        """PV unavailable (pv=0): BESS excluded from upward FAT."""
        fat = _MFRR_FAT_MODE_SWITCH_MIN
        got_no_pv  = fat_deliverable_mw(cfg, fat, current_net_mw=100.0, pv_available_mw=0.0)
        got_pv     = fat_deliverable_mw(cfg, fat, current_net_mw=100.0, pv_available_mw=1.0)
        bess_mw    = cfg.plant.bess.power_mw
        assert abs(got_pv - got_no_pv - bess_mw) < 1e-6, \
            f"PV flag should add exactly {bess_mw:.1f} MW (BESS): no_pv={got_no_pv:.2f} pv={got_pv:.2f}"

    def test_E5_pv_gating_threshold_exactly_001(self, cfg):
        """PV flag threshold is 0.01 MW: exactly at threshold counts as available."""
        fat = _MFRR_FAT_MODE_SWITCH_MIN
        got_at    = fat_deliverable_mw(cfg, fat, current_net_mw=100.0, pv_available_mw=0.01)
        got_below = fat_deliverable_mw(cfg, fat, current_net_mw=100.0, pv_available_mw=0.009)
        bess_mw   = cfg.plant.bess.power_mw
        assert got_at > got_below, "At threshold should include BESS, below should not"
        assert abs(got_at - got_below - bess_mw) < 1e-6

    def test_E6_down_direction_full_ramp_regardless_mode(self, cfg):
        """Down direction ignores operating mode — always full ramp."""
        fat = _AFRR_FAT_MODE_SWITCH_MIN
        expected = cfg.plant.psp.total_ramp_mw_per_min * fat + cfg.plant.bess.power_mw
        got = fat_deliverable_dn_mw(cfg, fat)
        assert abs(got - expected) < 1e-6


# ── Group F — build_reserve_offers ──────────────────────────────────────────

class TestBuildReserveOffers:

    def _build(self, cfg, committed_net, fat_min=12.5,
               headroom_fraction=1.0, reserved_up=None, reserved_dn=None,
               pv_available_mw=None):
        max_up = cfg.plant.p_max_generation_mw
        max_dn = cfg.plant.p_max_pump_mw
        prices_up = {h: 10.0 for h in committed_net}
        prices_dn = {h: 10.0 for h in committed_net}
        return build_reserve_offers(
            product="aFRR",
            committed_net=committed_net,
            cap_prices_up=prices_up,
            cap_prices_dn=prices_dn,
            cfg=cfg,
            fat_min=fat_min,
            max_up_mw=max_up,
            max_dn_mw=max_dn,
            headroom_fraction=headroom_fraction,
            reserved_up=reserved_up,
            reserved_dn=reserved_dn,
            pv_available_mw=pv_available_mw or {h: 5.0 for h in committed_net},
        )

    def test_F1_offer_up_respects_gen_cap(self, cfg):
        """offer_up + energy_position <= gen_cap (PR-11 up)."""
        p = cfg.plant
        fcr = max(0.0, p.fcr.mandatory_headroom_mw)
        gen_cap = p.p_max_generation_mw - fcr
        n = 300.0   # committed net MW (generating)
        offers = self._build(cfg, {1: n})
        assert offers[1].up_mw + n <= gen_cap + 1e-6, \
            f"PR-11 up violated: offer_up={offers[1].up_mw:.2f} + net={n} > gen_cap={gen_cap}"

    def test_F2_offer_dn_respects_pump_cap(self, cfg):
        """energy_position − offer_dn >= −pump_cap (PR-11 down)."""
        p = cfg.plant
        fcr = max(0.0, p.fcr.mandatory_headroom_mw)
        pump_cap = p.p_max_pump_mw - fcr
        n = -200.0   # pumping
        offers = self._build(cfg, {1: n})
        assert n - offers[1].dn_mw >= -pump_cap - 1e-6, \
            f"PR-11 dn violated: net={n} - offer_dn={offers[1].dn_mw:.2f} < -pump_cap={-pump_cap}"

    def test_F3_offer_up_respects_fat(self, cfg):
        """offer_up <= fat_deliverable_mw (PR-12 up)."""
        fat = _AFRR_FAT_MODE_SWITCH_MIN
        n = 200.0   # generating
        offers = self._build(cfg, {1: n}, fat_min=fat, pv_available_mw={1: 2.0})
        fat_cap = fat_deliverable_mw(cfg, fat, current_net_mw=n, pv_available_mw=2.0)
        assert offers[1].up_mw <= fat_cap + 1e-6, \
            f"PR-12 up violated: offer={offers[1].up_mw:.2f} > fat_cap={fat_cap:.2f}"

    def test_F4_offer_dn_respects_fat_dn(self, cfg):
        """offer_dn <= fat_deliverable_dn_mw (PR-12 down)."""
        fat = _AFRR_FAT_MODE_SWITCH_MIN
        fat_dn = fat_deliverable_dn_mw(cfg, fat)
        offers = self._build(cfg, {1: 100.0}, fat_min=fat)
        assert offers[1].dn_mw <= fat_dn + 1e-6, \
            f"PR-12 dn violated: offer={offers[1].dn_mw:.2f} > fat_dn={fat_dn:.2f}"

    def test_F5_headroom_fraction_scales_offer(self, cfg):
        """headroom_fraction=0.5 produces roughly half the offer vs fraction=1.0."""
        n = 0.0   # at zero net position (max headroom in both directions)
        full  = self._build(cfg, {1: n}, headroom_fraction=1.0)
        half  = self._build(cfg, {1: n}, headroom_fraction=0.5)
        # half offer should be <= full offer (may be further limited by FAT)
        assert half[1].up_mw <= full[1].up_mw + 1e-6
        assert half[1].dn_mw <= full[1].dn_mw + 1e-6

    def test_F6_reserved_up_reduces_available_headroom(self, cfg):
        """Prior aFRR reservation reduces headroom available for mFRR."""
        n = 0.0
        without_reserved = self._build(cfg, {1: n})
        with_reserved    = self._build(cfg, {1: n}, reserved_up={1: 50.0})
        assert with_reserved[1].up_mw <= without_reserved[1].up_mw + 1e-6, \
            "Reserved_up should reduce available up headroom"

    def test_F7_zero_headroom_hour_gives_zero_offer(self, cfg):
        """When energy position already at gen_cap, offer_up must be zero."""
        p = cfg.plant
        fcr = max(0.0, p.fcr.mandatory_headroom_mw)
        gen_cap = p.p_max_generation_mw - fcr
        offers = self._build(cfg, {1: gen_cap})
        assert offers[1].up_mw < 1e-6, \
            f"At gen_cap, offer_up should be 0, got {offers[1].up_mw:.4f}"

    def test_F8_all_hours_offered(self, cfg):
        """build_reserve_offers produces an offer for every input hour."""
        committed = {h: 100.0 for h in range(1, 25)}
        offers = self._build(cfg, committed)
        assert set(offers.keys()) == set(range(1, 25)), \
            f"Missing hours in offers: {set(range(1,25)) - set(offers.keys())}"


# ── Group G — check_reserve_offers ──────────────────────────────────────────

class TestCheckReserveOffers:

    def _make_offer(self, h, up_mw, dn_mw, price_up=10.0, price_dn=10.0):
        return {h: ReserveOffer(hour=h, up_mw=up_mw, dn_mw=dn_mw,
                                cap_price_up_eur_mw=price_up,
                                cap_price_dn_eur_mw=price_dn)}

    def test_G1_valid_offer_passes(self, cfg):
        """A valid, small offer passes the checker cleanly."""
        committed = {1: 100.0}
        offers = self._make_offer(1, up_mw=20.0, dn_mw=20.0)
        v = check_reserve_offers(offers, committed, cfg, fat_min=12.5,
                                 pv_available_mw={1: 2.0})
        assert v == [], f"Expected clean pass, got violations: {v}"

    def test_G2_pr11_up_violation_detected(self, cfg):
        """Offer_up that pushes total above gen_cap raises ReserveCheckError."""
        p = cfg.plant
        fcr = max(0.0, p.fcr.mandatory_headroom_mw)
        gen_cap = p.p_max_generation_mw - fcr
        # Position already at gen_cap, any positive offer breaches PR-11
        committed = {1: gen_cap}
        offers = self._make_offer(1, up_mw=10.0, dn_mw=0.0)
        with pytest.raises(ReserveCheckError, match="PR-11"):
            check_reserve_offers(offers, committed, cfg, fat_min=12.5)

    def test_G3_pr11_dn_violation_detected(self, cfg):
        """Offer_dn that pushes total below −pump_cap raises ReserveCheckError."""
        p = cfg.plant
        fcr = max(0.0, p.fcr.mandatory_headroom_mw)
        pump_cap = p.p_max_pump_mw - fcr
        committed = {1: -pump_cap}   # already at full pump
        offers = self._make_offer(1, up_mw=0.0, dn_mw=10.0)
        with pytest.raises(ReserveCheckError, match="PR-11"):
            check_reserve_offers(offers, committed, cfg, fat_min=12.5)

    def test_G4_pr12_fat_violation_detected(self, cfg):
        """Offer larger than FAT-deliverable raises ReserveCheckError."""
        fat = _AFRR_FAT_MODE_SWITCH_MIN
        fat_cap = fat_deliverable_mw(cfg, fat, current_net_mw=100.0, pv_available_mw=5.0)
        # Offer slightly above FAT cap
        offers = self._make_offer(1, up_mw=fat_cap + 10.0, dn_mw=0.0)
        with pytest.raises(ReserveCheckError, match="PR-12"):
            check_reserve_offers(offers, {1: 100.0}, cfg, fat_min=fat,
                                 pv_available_mw={1: 5.0})

    def test_G5_reserved_up_counts_toward_pr11(self, cfg):
        """Prior aFRR reservation + mFRR offer combined must not breach gen_cap."""
        p = cfg.plant
        fcr = max(0.0, p.fcr.mandatory_headroom_mw)
        gen_cap = p.p_max_generation_mw - fcr
        committed = {1: gen_cap - 30.0}   # 30 MW headroom
        reserved_up = {1: 25.0}            # aFRR already takes 25 MW
        # Trying to add 10 MW mFRR on top → 25+10=35 > 30 MW remaining → violation
        offers = self._make_offer(1, up_mw=10.0, dn_mw=0.0)
        with pytest.raises(ReserveCheckError, match="PR-11"):
            check_reserve_offers(offers, committed, cfg, fat_min=12.5,
                                 reserved_up=reserved_up)

    def test_G6_negative_reserve_offer_detected(self, cfg):
        """Negative up or dn offer is caught by the checker."""
        offers = self._make_offer(1, up_mw=-5.0, dn_mw=10.0)
        with pytest.raises(ReserveCheckError, match="negative"):
            check_reserve_offers(offers, {1: 100.0}, cfg, fat_min=12.5)

    def test_G7_zero_offer_always_passes(self, cfg):
        """Zero offer (not participating) always passes the checker."""
        committed = {h: 0.0 for h in range(1, 25)}
        offers = {h: ReserveOffer(hour=h, up_mw=0.0, dn_mw=0.0,
                                  cap_price_up_eur_mw=10.0, cap_price_dn_eur_mw=10.0)
                  for h in range(1, 25)}
        v = check_reserve_offers(offers, committed, cfg, fat_min=12.5)
        assert v == []

    def test_G8_price_cap_violation_detected(self, cfg):
        """Cap price above aFRR max (250 EUR/MW) is caught when cap_price_max passed."""
        offers = self._make_offer(1, up_mw=10.0, dn_mw=10.0, price_up=300.0)
        with pytest.raises(ReserveCheckError):
            check_reserve_offers(offers, {1: 100.0}, cfg, fat_min=12.5,
                                 cap_price_max=250.0)

    def test_G9_pv_gated_offer_passes_checker(self, cfg):
        """Offer sized without BESS (PV=0) and capped at headroom should pass checker.

        With PV=0, BESS is excluded from upward FAT. The valid offer is bounded
        by min(FAT-cap-no-pv, headroom). We use 90% of that safe bound.
        """
        p = cfg.plant
        fcr = max(0.0, p.fcr.mandatory_headroom_mw)
        gen_cap = p.p_max_generation_mw - fcr
        fat = _MFRR_FAT_MODE_SWITCH_MIN
        net = 100.0
        fat_cap_no_pv = fat_deliverable_mw(cfg, fat, current_net_mw=net,
                                            pv_available_mw=0.0)
        headroom = gen_cap - net    # available up headroom
        safe_offer = min(fat_cap_no_pv, headroom) * 0.9
        offers = self._make_offer(1, up_mw=safe_offer, dn_mw=10.0)
        # Check with pv_available_mw=0 so checker uses same (no-BESS) cap
        v = check_reserve_offers(offers, {1: net}, cfg, fat_min=fat,
                                 pv_available_mw={1: 0.0})
        assert v == [], f"PV-gated offer should pass, got: {v}"


# ── Group H — Operational analytics (pure computation) ──────────────────────

class TestOperationalAnalytics:

    def _mock_schedule(self):
        """Build a minimal 6-hour mock schedule for analytics tests."""
        H = list(range(1, 7))
        psp = {
            1: {"turbine_mw": 259.2, "pump_mw": 0.0, "units_on_turb": [1,1,0,0], "units_on_pump": [0,0,0,0]},
            2: {"turbine_mw": 259.2, "pump_mw": 0.0, "units_on_turb": [1,1,0,0], "units_on_pump": [0,0,0,0]},
            3: {"turbine_mw": 0.0,   "pump_mw": 223.2, "units_on_turb": [0,0,0,0], "units_on_pump": [0,0,1,1]},
            4: {"turbine_mw": 0.0,   "pump_mw": 223.2, "units_on_turb": [0,0,0,0], "units_on_pump": [0,0,1,1]},
            5: {"turbine_mw": 259.2, "pump_mw": 0.0, "units_on_turb": [1,1,0,0], "units_on_pump": [0,0,0,0]},
            6: {"turbine_mw": 259.2, "pump_mw": 0.0, "units_on_turb": [1,1,0,0], "units_on_pump": [0,0,0,0]},
        }
        bess = {h: {"charge_mw": 0.0, "discharge_mw": 0.0, "soc_mwh": 1.0} for h in H}
        pv   = {h: {"used_mw": 2.0, "available_mw": 2.0, "to_bess_mw": 0.0, "curtailed_mw": 0.0} for h in H}
        prices = {1: 80.0, 2: 75.0, 3: 25.0, 4: 20.0, 5: 70.0, 6: 65.0}
        return psp, bess, pv, prices

    def test_H1_operational_patterns_returns_expected_keys(self):
        from phase_5d_analytics_and_reporting.analytics_and_kpis.operational_analytics import (
            compute_operational_patterns,
        )
        psp, bess, pv, prices = self._mock_schedule()
        result = compute_operational_patterns(psp, bess, prices)
        required_keys = [
            "turbine_hours_total", "pump_hours_total",
            "turbine_starts_total", "pump_starts_total",
            "turb_avg_run_h", "turb_max_run_h",
        ]
        for k in required_keys:
            assert k in result, f"Missing key '{k}' in operational patterns"

    def test_H2_turbine_hours_correct(self):
        from phase_5d_analytics_and_reporting.analytics_and_kpis.operational_analytics import (
            compute_operational_patterns,
        )
        psp, bess, pv, prices = self._mock_schedule()
        result = compute_operational_patterns(psp, bess, prices)
        # Hours 1,2,5,6 = turbine on (4 hours)
        assert result["turbine_hours_total"] == 4, \
            f"Expected 4 turbine hours, got {result['turbine_hours_total']}"

    def test_H3_pump_hours_correct(self):
        from phase_5d_analytics_and_reporting.analytics_and_kpis.operational_analytics import (
            compute_operational_patterns,
        )
        psp, bess, pv, prices = self._mock_schedule()
        result = compute_operational_patterns(psp, bess, prices)
        # Hours 3,4 = pump on (2 hours)
        assert result["pump_hours_total"] == 2, \
            f"Expected 2 pump hours, got {result['pump_hours_total']}"

    def test_H4_temporal_patterns_band_structure(self):
        from phase_5d_analytics_and_reporting.analytics_and_kpis.operational_analytics import (
            compute_temporal_patterns,
        )
        psp, bess, pv, prices = self._mock_schedule()
        result = compute_temporal_patterns(psp, pv, prices)
        # All 4 bands must be present (some may be empty dicts if no hours)
        for band in ("night", "morning", "afternoon", "evening"):
            assert band in result, f"Missing band '{band}' in temporal patterns"

    def test_H5_frr_strategy_pv_gating_zero_pv(self):
        from phase_5d_analytics_and_reporting.analytics_and_kpis.operational_analytics import (
            compute_frr_strategy_metrics,
        )
        pv_schedule = {h: {"available_mw": 0.0} for h in range(1, 25)}
        result = compute_frr_strategy_metrics(pv_schedule=pv_schedule)
        gating = result.get("bess_pv_gating", {})
        assert gating.get("hours_pv_unavailable") == 24, \
            f"Expected 24 hours PV unavailable, got {gating.get('hours_pv_unavailable')}"
        assert gating.get("bess_up_frr_blocked_hours") == 24

    def test_H6_frr_strategy_pv_gating_half_day(self):
        from phase_5d_analytics_and_reporting.analytics_and_kpis.operational_analytics import (
            compute_frr_strategy_metrics,
        )
        # PV available for hours 8-18 (11 hours), zero otherwise
        pv_schedule = {h: {"available_mw": 3.0 if 8 <= h <= 18 else 0.0}
                       for h in range(1, 25)}
        result = compute_frr_strategy_metrics(pv_schedule=pv_schedule)
        gating = result["bess_pv_gating"]
        assert gating["hours_pv_available"] == 11
        assert gating["hours_pv_unavailable"] == 13
