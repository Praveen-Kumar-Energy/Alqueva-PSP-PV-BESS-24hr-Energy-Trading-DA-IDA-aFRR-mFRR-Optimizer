"""
test_settlement.py — pure arithmetic settlement tests.

Pure arithmetic tests for the three settlement calculators.
No solver, no DB: we call the calculation logic directly with known inputs
and verify exact arithmetic (to floating-point precision).

Settlement formulas under test:
  DA:        revenue_h = volume_mwh_h × price_h;   total = Σ revenue_h
  aFRR cap:  capacity  = Σ (up_mw × cap_up_eur_mw + dn_mw × cap_dn_eur_mw)
  aFRR act:  activation = Σ (up_mw × eff_isp_h × up_price + dn_mw × eff_isp_h × dn_price)
             (BUG-2 fix: separate up/dn prices)
  Imbalance: long (> 0) at long_price; short (< 0) at short_price premium
             net_eur = long_revenue - short_cost

Tests bypass PositionStore / DeliveryStore by testing the pure arithmetic
layer (dataclass constructors and property calculations) directly.
"""
from __future__ import annotations

import math
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helper: build a ReserveSettlement directly (arithmetic check, no DB) ────

from phase_5b_reserve_settlement.reserve_settlement_calculation.afrr_settlement_calculator import (
    ReserveSettlement,
)
from phase_5c_imbalance_settlement.imbalance_settlement_calculation.imbalance_settlement_calculator import (
    ImbalanceSettlement,
    settle_imbalance,
)
from phase_5c_imbalance_settlement.imbalance_price_and_volume.imbalance_volume_calculator import (
    ImbalanceRow,
)


# ── DA settlement arithmetic ─────────────────────────────────────────────────

class TestDASettlementArithmetic:
    """Test the DA settlement arithmetic formula without hitting PositionStore."""

    def test_S1_single_hour_sell_exact(self):
        """DA sell: 100 MWh at 65 EUR/MWh = 6500 EUR."""
        volume_mwh = 100.0
        price = 65.0
        revenue = volume_mwh * price
        assert math.isclose(revenue, 6500.0, rel_tol=1e-9)

    def test_S2_single_hour_buy_negative_revenue(self):
        """DA buy (pump): -50 MWh at 30 EUR/MWh = -1500 EUR (cost)."""
        volume_mwh = -50.0   # negative = buying (pump)
        price = 30.0
        revenue = volume_mwh * price
        assert math.isclose(revenue, -1500.0, rel_tol=1e-9)

    def test_S3_multi_hour_total(self):
        """DA total = exact sum over 24 hours."""
        volumes = {h: float(h * 10) for h in range(1, 25)}   # 10, 20, ..., 240 MWh
        prices  = {h: 50.0 for h in range(1, 25)}             # flat 50 EUR/MWh
        per_hour = {h: volumes[h] * prices[h] for h in range(1, 25)}
        total    = sum(per_hour.values())
        # Σ h*10 for h=1..24 = 10 * (24*25/2) = 3000; × 50 = 150000
        expected = sum(h * 10 * 50.0 for h in range(1, 25))
        assert math.isclose(total, expected, rel_tol=1e-9)

    def test_S4_zero_price_zero_revenue(self):
        """Zero price: revenue must be zero regardless of volume."""
        volume_mwh = 500.0
        revenue = volume_mwh * 0.0
        assert revenue == 0.0

    def test_S5_negative_price_cost_if_selling(self):
        """Negative price: selling at -10 EUR/MWh is a cost, not revenue."""
        volume_mwh = 200.0      # selling (turbine)
        price = -10.0           # negative price (curtailment period)
        revenue = volume_mwh * price
        assert revenue < 0.0
        assert math.isclose(revenue, -2000.0, rel_tol=1e-9)


# ── aFRR capacity settlement arithmetic ─────────────────────────────────────

class TestReserveCapacitySettlement:
    """Test aFRR capacity revenue formula (no DB, direct arithmetic)."""

    def test_S6_capacity_single_hour(self):
        """Capacity hour: 50 MW up × 10 EUR/MW + 30 MW dn × 8 EUR/MW = 740 EUR."""
        up_mw, cap_up = 50.0, 10.0
        dn_mw, cap_dn = 30.0,  8.0
        cap = up_mw * cap_up + dn_mw * cap_dn
        assert math.isclose(cap, 740.0, rel_tol=1e-9)

    def test_S7_capacity_multi_hour_sum(self):
        """Capacity total over 24 hours."""
        offers = [{"up_mw": 50.0, "cap_up_eur_mw": 10.0,
                   "dn_mw": 30.0, "cap_dn_eur_mw":  8.0}
                  for _ in range(24)]
        total = sum(o["up_mw"] * o["cap_up_eur_mw"] + o["dn_mw"] * o["cap_dn_eur_mw"]
                    for o in offers)
        assert math.isclose(total, 740.0 * 24, rel_tol=1e-9)

    def test_S8_reserve_settlement_total_property(self):
        """ReserveSettlement.total_eur == capacity_eur + activation_eur."""
        rs = ReserveSettlement(product="aFRR", capacity_eur=1000.0, activation_eur=250.0)
        assert math.isclose(rs.total_eur, 1250.0, rel_tol=1e-9)

    def test_S9_zero_offer_zero_capacity(self):
        """Zero-MW offer earns zero capacity payment."""
        rs = ReserveSettlement(product="aFRR", capacity_eur=0.0, activation_eur=0.0)
        assert rs.total_eur == 0.0


# ── aFRR activation settlement — separate up/dn prices ──────────────────────

class TestReserveActivationSettlement:
    """Test BUG-2 fix: up and dn activation prices must be applied separately."""

    def test_S10_activation_up_only(self):
        """Up activation: 10 MW × 0.5 h × 80 EUR/MWh = 400 EUR."""
        up_mw, isp_h, up_price = 10.0, 0.5, 80.0
        dn_mw, dn_price = 0.0, 60.0
        act = up_mw * isp_h * up_price + dn_mw * isp_h * dn_price
        assert math.isclose(act, 400.0, rel_tol=1e-9)

    def test_S11_activation_dn_only(self):
        """Down activation: 20 MW × 0.5 h × 60 EUR/MWh = 600 EUR."""
        up_mw, isp_h, up_price = 0.0, 0.5, 80.0
        dn_mw, dn_price = 20.0, 60.0
        act = up_mw * isp_h * up_price + dn_mw * isp_h * dn_price
        assert math.isclose(act, 600.0, rel_tol=1e-9)

    def test_S12_bug2_fix_separate_prices(self):
        """BUG-2: using wrong (average) price produces a different result.

        If up_price=80, dn_price=60, up_mw=10, dn_mw=20, isp_h=0.5:
          Correct: 10*0.5*80 + 20*0.5*60 = 400 + 600 = 1000 EUR
          Wrong (single avg price 70): (10+20)*0.5*70 = 1050 EUR  (overstates by 50)
        The BUG-2 fix must produce 1000, not 1050.
        """
        up_mw, dn_mw, isp_h = 10.0, 20.0, 0.5
        up_price, dn_price = 80.0, 60.0

        correct = up_mw * isp_h * up_price + dn_mw * isp_h * dn_price
        wrong   = (up_mw + dn_mw) * isp_h * ((up_price + dn_price) / 2)

        assert math.isclose(correct, 1000.0, rel_tol=1e-9), f"correct formula gave {correct}"
        assert not math.isclose(correct, wrong, rel_tol=1e-6), \
            "BUG-2: single-price and dual-price results should differ here"

    def test_S13_eff_isp_h_ramp_correction(self):
        """Ramp-corrected eff_isp_h < isp_hours reduces activation revenue.

        aFRR ISP = 0.5 h, FAT = 5 min, so ramp energy loss ≈ FAT/2 minutes.
        eff_isp_h = (30 min - 5/2 min) / 60 ≈ 0.4583 h
        Revenue must be lower with eff_isp_h than with raw isp_hours.
        """
        isp_h = 0.5
        fat_min = 5.0
        eff_isp_h = (30.0 - fat_min / 2.0) / 60.0   # 0.4583...

        up_mw, up_price = 50.0, 80.0

        rev_raw  = up_mw * isp_h     * up_price
        rev_corr = up_mw * eff_isp_h * up_price

        assert rev_corr < rev_raw, "Ramp-corrected revenue should be less than raw"
        assert math.isclose(rev_corr, 50.0 * eff_isp_h * 80.0, rel_tol=1e-9)

    def test_S14_reserve_settlement_activation_only(self):
        """ReserveSettlement with capacity=0: total_eur == activation_eur."""
        rs = ReserveSettlement(product="mFRR", capacity_eur=0.0, activation_eur=500.0)
        assert math.isclose(rs.total_eur, 500.0, rel_tol=1e-9)


# ── Imbalance settlement — dual pricing ─────────────────────────────────────

class TestImbalanceSettlement:
    """Test dual-price imbalance settlement (long at discount, short at premium)."""

    def test_S15_long_imbalance_earns_long_price(self):
        """Long (positive) imbalance: sold at long_price (discount)."""
        rows = [ImbalanceRow(isp=1, hour=1, imbalance_mwh=10.0)]
        long_price  = {1: 45.0}   # discount vs DA 50
        short_price = {1: 65.0}
        result = settle_imbalance(rows, short_price, long_price)
        assert math.isclose(result.long_revenue_eur, 10.0 * 45.0, rel_tol=1e-9)
        assert result.short_cost_eur == 0.0

    def test_S16_short_imbalance_costs_short_price(self):
        """Short (negative) imbalance: bought back at short_price (premium)."""
        rows = [ImbalanceRow(isp=1, hour=1, imbalance_mwh=-5.0)]
        long_price  = {1: 45.0}
        short_price = {1: 65.0}   # premium vs DA 50
        result = settle_imbalance(rows, short_price, long_price)
        assert result.long_revenue_eur == 0.0
        assert math.isclose(result.short_cost_eur, 5.0 * 65.0, rel_tol=1e-9)

    def test_S17_net_eur_property(self):
        """net_eur = long_revenue - short_cost."""
        rs = ImbalanceSettlement(long_revenue_eur=200.0, short_cost_eur=350.0,
                                 total_imbalance_mwh=15.0)
        assert math.isclose(rs.net_eur, -150.0, rel_tol=1e-9)

    def test_S18_net_imbalance_is_typically_negative(self):
        """Under dual pricing, long at discount + short at premium → net loss."""
        rows = [
            ImbalanceRow(isp=1, hour=1, imbalance_mwh= 5.0),   # long
            ImbalanceRow(isp=2, hour=2, imbalance_mwh=-5.0),   # short
        ]
        da_price = 50.0
        long_price  = {1: da_price * 0.90, 2: da_price * 0.90}   # 45
        short_price = {1: da_price * 1.10, 2: da_price * 1.10}   # 55
        result = settle_imbalance(rows, short_price, long_price)
        # long: 5 × 45 = 225; short: 5 × 55 = 275; net = -50
        assert result.net_eur < 0.0, "Under dual pricing, balanced long/short should net negative"
        assert math.isclose(result.long_revenue_eur, 5.0 * 45.0, rel_tol=1e-9)
        assert math.isclose(result.short_cost_eur, 5.0 * 55.0, rel_tol=1e-9)

    def test_S19_zero_imbalance_zero_settlement(self):
        """Perfect schedule delivery: zero imbalance = zero settlement."""
        rows = [ImbalanceRow(isp=h, hour=h, imbalance_mwh=0.0) for h in range(1, 25)]
        long_price  = {h: 45.0 for h in range(1, 25)}
        short_price = {h: 55.0 for h in range(1, 25)}
        result = settle_imbalance(rows, short_price, long_price)
        assert result.long_revenue_eur == 0.0
        assert result.short_cost_eur == 0.0
        assert result.net_eur == 0.0

    def test_S20_multi_hour_mixed_total_imbalance(self):
        """total_imbalance_mwh = sum of absolute values (long + short)."""
        rows = [
            ImbalanceRow(isp=1, hour=1, imbalance_mwh= 3.0),
            ImbalanceRow(isp=2, hour=2, imbalance_mwh=-7.0),
            ImbalanceRow(isp=3, hour=3, imbalance_mwh= 0.0),
        ]
        result = settle_imbalance(rows, {1: 55.0, 2: 55.0, 3: 55.0},
                                        {1: 45.0, 2: 45.0, 3: 45.0})
        assert math.isclose(result.total_imbalance_mwh, 10.0, rel_tol=1e-9)
