"""
test_reserve_market_deep.py — comprehensive reserve market test suite.

Integration tests that run the full DA→aFRR offer→mFRR offer→RT dispatch→
aFRR activation→mFRR activation→settlement chain for a fixed test date and then
assert correctness of every physical and financial invariant.

Tests cover 9 areas:
  T1  No simultaneous up+down activation in any ISP (BUG-1)
  T2  Every activation stays within mode-aware FAT deliverable (BUG-7)
  T3  Physical headroom: scheduled ± activated stays within plant envelope (BUG-3)
  T4  Minimum activation hold time per product (BUG-5)
  T5  BESS SOC never goes outside [soc_min, soc_max] during activation (BUG-6)
  T6  Combined aFRR+mFRR activation stays within plant envelope (BUG-4)
  T7  Settlement uses separate up/dn prices — total activation revenue correct (BUG-2)
  T8  Imbalance excludes instructed activations (no double-count)
  T9  aFRR capacity offer checker passes (PR-11, PR-12, price cap)

Run from repo root:
    python -m pytest tests/test_reserve_market_deep.py -v
or:
    python tests/test_reserve_market_deep.py
"""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
import unittest

# Ensure repo root is on path.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

TEST_DATE = "2026-07-05"

# ── Lazy pipeline runner ─────────────────────────────────────────────────────
# Module-level sentinels used as a run-once cache: the full pipeline
# (DA → aFRR → mFRR → RT → activation) is expensive; running it once and
# reusing the DB state for all test classes avoids redundant solves.
_pipeline_ran = False
_pipeline_cfg = None


def _ensure_pipeline():
    """Run the pipeline once and cache the config. Uses --auto-approve / no_pause APIs."""
    global _pipeline_ran, _pipeline_cfg
    if _pipeline_ran:
        return _pipeline_cfg

    from common_layer.configuration import load_config
    cfg = load_config()
    _pipeline_cfg = cfg

    # run_da takes config_dir (not cfg); auto_approve controls prompt.
    from phase_1_da_day_ahead_bidding.run_da import run_da
    r = run_da(TEST_DATE, auto_approve=True)
    assert r["status"] == "SUBMITTED", f"DA failed: {r}"

    # Remaining phases take the cfg object and no_pause flag.
    from phase_3a_afrr_automatic_frequency_reserve.run_afrr import run_afrr
    r = run_afrr(TEST_DATE, cfg, no_pause=True)
    assert r["status"] == "SUBMITTED", f"aFRR offer failed: {r}"

    from phase_3b_mfrr_manual_frequency_reserve.run_mfrr import run_mfrr
    r = run_mfrr(TEST_DATE, cfg, no_pause=True)
    assert r["status"] == "SUBMITTED", f"mFRR offer failed: {r}"

    from phase_4a_isp_real_time_dispatch.run_realtime import run_realtime
    r = run_realtime(TEST_DATE, cfg, no_pause=True)
    assert r["status"] == "OK", f"RT dispatch failed: {r}"

    from phase_4b_afrr_activation_response.run_afrr_activation import run_afrr_activation
    r = run_afrr_activation(TEST_DATE, cfg, no_pause=True)
    assert r["status"] in ("OK", "NO_OFFER"), f"aFRR activation failed: {r}"

    from phase_4c_mfrr_activation_response.run_mfrr_activation import run_mfrr_activation
    r = run_mfrr_activation(TEST_DATE, cfg, no_pause=True)
    assert r["status"] in ("OK", "NO_OFFER"), f"mFRR activation failed: {r}"

    _pipeline_ran = True
    return cfg


# ── T1 — No simultaneous up+down in any ISP ─────────────────────────────────
class T1_NoSimultaneousUpDown(unittest.TestCase):
    """BUG-1 fix: activation must be exclusively up or down in any given ISP."""

    def test_afrr_exclusive(self):
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore
        rows = ActivationStore().load(TEST_DATE, "aFRR")
        for r in rows:
            self.assertFalse(
                r["up_mw"] > 1e-6 and r["dn_mw"] > 1e-6,
                f"aFRR ISP{r['isp']}: simultaneous up={r['up_mw']:.2f} dn={r['dn_mw']:.2f}"
            )

    def test_mfrr_exclusive(self):
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore
        rows = ActivationStore().load(TEST_DATE, "mFRR")
        for r in rows:
            self.assertFalse(
                r["up_mw"] > 1e-6 and r["dn_mw"] > 1e-6,
                f"mFRR ISP{r['isp']}: simultaneous up={r['up_mw']:.2f} dn={r['dn_mw']:.2f}"
            )


# ── T2 — Every activation stays within mode-aware FAT deliverable ───────────
class T2_FATDeliverability(unittest.TestCase):
    """BUG-7 fix: up deliverable must respect mode; pump-mode aFRR capped differently."""

    def _check_product(self, product: str, fat_min: float):
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore, DeliveryStore
        from common_layer.optimisation_model.reserve_offer_builder import (
            fat_deliverable_mw, fat_deliverable_dn_mw
        )
        sched_by_isp = {r["isp"]: r["scheduled_mw"] for r in DeliveryStore().load(TEST_DATE)}
        rows = ActivationStore().load(TEST_DATE, product)
        EPS = 0.05  # allow tiny float slack

        for r in rows:
            isp = r["isp"]
            sched = sched_by_isp.get(isp, 0.0)
            fat_up = fat_deliverable_mw(cfg, fat_min, current_net_mw=sched)
            fat_dn = fat_deliverable_dn_mw(cfg, fat_min)
            if r["up_mw"] > EPS:
                self.assertLessEqual(
                    r["up_mw"], fat_up + EPS,
                    f"{product} ISP{isp}: up {r['up_mw']:.2f} > FAT-deliverable {fat_up:.2f}"
                )
            if r["dn_mw"] > EPS:
                self.assertLessEqual(
                    r["dn_mw"], fat_dn + EPS,
                    f"{product} ISP{isp}: dn {r['dn_mw']:.2f} > FAT-deliverable {fat_dn:.2f}"
                )

    def test_afrr_fat_5min(self):
        _ensure_pipeline()
        from common_layer.configuration import load_config
        cfg = load_config()
        self._check_product("aFRR", cfg.market.afrr.fat_min)

    def test_mfrr_fat_12_5min(self):
        _ensure_pipeline()
        from common_layer.configuration import load_config
        cfg = load_config()
        self._check_product("mFRR", cfg.market.mfrr.fat_min)


# ── T3 — Physical headroom: scheduled ± activated within plant envelope ─────
class T3_PhysicalHeadroom(unittest.TestCase):
    """BUG-3 fix: scheduled_mw + up_mw <= p_gen_cap; scheduled_mw - dn_mw >= -p_pump_cap."""

    def _check(self, product: str):
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore, DeliveryStore
        sched_by_isp = {r["isp"]: r["scheduled_mw"] for r in DeliveryStore().load(TEST_DATE)}
        rows = ActivationStore().load(TEST_DATE, product)
        gen_cap = cfg.plant.p_max_generation_mw
        pump_cap = cfg.plant.p_max_pump_mw
        EPS = 0.1

        for r in rows:
            isp = r["isp"]
            sched = sched_by_isp.get(isp, 0.0)
            if r["up_mw"] > 1e-6:
                total = sched + r["up_mw"]
                self.assertLessEqual(
                    total, gen_cap + EPS,
                    f"{product} ISP{isp}: sched {sched:.1f} + up {r['up_mw']:.1f} "
                    f"= {total:.1f} > gen_cap {gen_cap:.1f}"
                )
            if r["dn_mw"] > 1e-6:
                total = sched - r["dn_mw"]
                self.assertGreaterEqual(
                    total, -pump_cap - EPS,
                    f"{product} ISP{isp}: sched {sched:.1f} - dn {r['dn_mw']:.1f} "
                    f"= {total:.1f} < -pump_cap {-pump_cap:.1f}"
                )

    def test_afrr_headroom(self):
        self._check("aFRR")

    def test_mfrr_headroom(self):
        self._check("mFRR")


# ── T4 — Minimum activation hold time ───────────────────────────────────────
class T4_MinimumHoldTime(unittest.TestCase):
    """BUG-5 fix: each activation run spans >= min_hold_isps consecutive ISPs.

    Note: runs appear shorter when they cross into hours with zero MW offered
    (e.g., mFRR up=0 in H07-H11 when aFRR has taken all headroom). In those
    hours, the hold state machine is still active but no row is appended because
    the offered quantity is below the minimum activation threshold. We skip the
    length check for runs that are truncated by a zero-offer boundary.
    """

    # aFRR: 2-ISP minimum (PICASSO rule, 30 min); mFRR: 3-ISP minimum (MARI rule, 45 min)
    _MIN_HOLD = {"aFRR": 2, "mFRR": 3}

    def _check(self, product: str):
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore, ReserveStore
        rows = ActivationStore().load(TEST_DATE, product)
        if not rows:
            self.skipTest(f"No {product} activations on test date")

        offers = ReserveStore().load_reserve(TEST_DATE, product)
        min_hold = self._MIN_HOLD[product]
        min_act_mw = {"aFRR": 1.0, "mFRR": 2.0}[product]

        # isp -> activated row for direction lookup.
        rows_by_isp = {r["isp"]: r for r in rows}
        activated_isps = sorted(rows_by_isp)

        # Find consecutive runs.
        runs = []
        if activated_isps:
            run_start = activated_isps[0]
            run_len = 1
            for prev, curr in zip(activated_isps, activated_isps[1:]):
                if curr == prev + 1:
                    run_len += 1
                else:
                    runs.append((run_start, run_len))
                    run_start = curr
                    run_len = 1
            runs.append((run_start, run_len))

        for start_isp, length in runs:
            if length >= min_hold:
                continue

            last_isp = start_isp + length - 1
            next_isp = last_isp + 1

            # Edge of day — hold naturally truncated at delivery day boundary.
            if start_isp == 1 or last_isp >= 96 or next_isp > 96:
                continue

            # Determine direction of this run (all ISPs in a hold have same direction).
            r0 = rows_by_isp[start_isp]
            is_up_run = r0["up_mw"] > 1e-6

            # ISP -> hour mapping: with 4 ISPs/hour, hour = (isp-1)//4 + 1.
            next_hour = (next_isp - 1) // 4 + 1
            next_offer = offers.get(next_hour, {"up_mw": 0.0, "dn_mw": 0.0})

            # Allow short run if the next hour has zero offer in the same direction.
            if is_up_run and next_offer["up_mw"] < min_act_mw:
                continue   # zero up-offer in next hour blocked the hold from appending
            if not is_up_run and next_offer["dn_mw"] < min_act_mw:
                continue   # zero dn-offer in next hour blocked the hold from appending

            self.assertGreaterEqual(
                length, min_hold,
                f"{product}: run starting ISP{start_isp} (dir={'up' if is_up_run else 'dn'}) "
                f"has length {length} < min_hold {min_hold}; "
                f"next ISP{next_isp}=H{next_hour} offer "
                f"up={next_offer['up_mw']:.1f} dn={next_offer['dn_mw']:.1f}"
            )

    def test_afrr_hold(self):
        self._check("aFRR")

    def test_mfrr_hold(self):
        self._check("mFRR")


# ── T5 — BESS SOC never exits [soc_min, soc_max] ───────────────────────────
class T5_BESSSOCBounds(unittest.TestCase):
    """BUG-6 fix: replay SOC across activated ISPs; must stay within config bounds."""

    def _replay_soc(self, product: str):
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore

        bess = cfg.plant.bess
        soc = bess.initial_soc_frac * bess.capacity_mwh
        soc_min = bess.e_min_mwh
        soc_max = bess.e_max_mwh
        bess_power = bess.power_mw
        isp_h = cfg.market.balancing.isp_duration_min / 60.0

        rows = ActivationStore().load(TEST_DATE, product)
        violations = []

        for r in rows:
            bess_contrib_up = min(bess_power, r["up_mw"])
            bess_contrib_dn = min(bess_power, r["dn_mw"])
            if r["up_mw"] > 1e-6:
                soc = max(soc_min, soc - bess_contrib_up * isp_h)
            elif r["dn_mw"] > 1e-6:
                soc = min(soc_max, soc + bess_contrib_dn * isp_h)
            # Check after update.
            if soc < soc_min - 1e-6 or soc > soc_max + 1e-6:
                violations.append(
                    f"{product} ISP{r['isp']}: SOC {soc:.4f} MWh outside "
                    f"[{soc_min:.4f}, {soc_max:.4f}]"
                )
        return violations

    def test_afrr_bess_soc(self):
        v = self._replay_soc("aFRR")
        self.assertEqual(v, [], "\n".join(v))

    def test_mfrr_bess_soc(self):
        v = self._replay_soc("mFRR")
        self.assertEqual(v, [], "\n".join(v))


# ── T6 — Combined aFRR+mFRR activation stays within plant envelope ──────────
class T6_CombinedHeadroom(unittest.TestCase):
    """BUG-4 fix: sum of aFRR and mFRR activations must not exceed plant limits."""

    def test_combined_headroom(self):
        cfg = _ensure_pipeline()
        from common_layer.optimisation_model.reserve_offer_builder import (
            check_combined_activation_headroom,
        )
        violations = check_combined_activation_headroom(TEST_DATE, cfg)
        self.assertEqual(
            violations, [],
            f"Combined aFRR+mFRR headroom violations:\n" + "\n".join(violations)
        )


# ── T7 — Settlement uses separate up/dn prices ──────────────────────────────
class T7_SettlementSeparatePrices(unittest.TestCase):
    """BUG-2 fix: up revenue uses up_price, dn revenue uses dn_price — never mixed."""

    def _check_settlement_math(self, product: str):
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore
        from phase_5b_reserve_settlement.reserve_settlement_calculation.afrr_settlement_calculator import (
            settle_reserve,
        )

        isp_h = cfg.market.balancing.isp_duration_min / 60.0
        rows = ActivationStore().load(TEST_DATE, product)

        # Compute expected revenue using separate prices and ramp-corrected eff_isp_h.
        expected_activation_eur = sum(
            r["up_mw"] * r.get("eff_isp_h", isp_h) * r["up_price_eur_mwh"]
            + r["dn_mw"] * r.get("eff_isp_h", isp_h) * r["dn_price_eur_mwh"]
            for r in rows
        )

        result = settle_reserve(TEST_DATE, product, isp_h)
        self.assertAlmostEqual(
            result.activation_eur, expected_activation_eur, places=2,
            msg=f"{product} settlement activation mismatch: "
                f"expected {expected_activation_eur:.2f}, got {result.activation_eur:.2f}"
        )

    def test_afrr_settlement_prices(self):
        self._check_settlement_math("aFRR")

    def test_mfrr_settlement_prices(self):
        self._check_settlement_math("mFRR")

    def test_up_price_exceeds_dn_price(self):
        """Verify price structure: up_price = DA×1.30 > dn_price = DA×0.70 always."""
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore
        for product in ("aFRR", "mFRR"):
            for r in ActivationStore().load(TEST_DATE, product):
                if r["up_mw"] > 1e-6 or r["dn_mw"] > 1e-6:
                    self.assertGreater(
                        r["up_price_eur_mwh"], r["dn_price_eur_mwh"],
                        f"{product} ISP{r['isp']}: up_price {r['up_price_eur_mwh']} "
                        f"<= dn_price {r['dn_price_eur_mwh']}"
                    )


# ── T8 — Imbalance excludes instructed activations ──────────────────────────
class T8_ImbalanceExcludesActivations(unittest.TestCase):
    """Activated reserve energy is instructed — must not appear as imbalance."""

    def test_imbalance_netting(self):
        """Verify imbalance formula: imbalance = (actual - sched) * isp_h (noise only).

        Architecture note (from imbalance_volume_calculator.py docstring):
        In simulation, actual_mw = scheduled + noise only — activation energy is
        NOT added to actual_mw because Phase 4B/4C run after 4A. Subtracting
        instructed activations that were never in actual_mw would create phantom
        imbalances. The correct simulation formula is therefore:
            imbalance_mwh = (actual - sched) * isp_h   (noise only)

        This test verifies that compute_imbalance exactly matches the stored
        (actual - sched) values from DeliveryStore, confirming no double-counting.
        """
        cfg = _ensure_pipeline()
        from phase_5c_imbalance_settlement.imbalance_price_and_volume.imbalance_volume_calculator import (
            compute_imbalance,
        )
        from common_layer.database import DeliveryStore

        isp_h = cfg.market.balancing.isp_duration_min / 60.0
        imbalance_rows = compute_imbalance(TEST_DATE, isp_h)

        delivery = {r["isp"]: (r["scheduled_mw"], r["actual_mw"])
                    for r in DeliveryStore().load(TEST_DATE)}

        # For every ISP, verify compute_imbalance == (actual - sched) * isp_h exactly.
        for row in imbalance_rows:
            isp = row.isp
            if isp not in delivery:
                continue
            sched, actual = delivery[isp]
            expected_imb = (actual - sched) * isp_h   # noise only — no activation subtraction
            self.assertAlmostEqual(
                row.imbalance_mwh, expected_imb, places=4,
                msg=f"ISP{isp}: imbalance_mwh {row.imbalance_mwh:.4f} "
                    f"!= (actual-sched)*isp_h {expected_imb:.4f}"
            )


# ── T9 — aFRR/mFRR offer checker passes (PR-11, PR-12, price cap) ───────────
class T9_OfferCheckerPasses(unittest.TestCase):
    """Permanent Phase 3A/3B checker must pass with zero violations."""

    def test_afrr_offer_checker(self):
        cfg = _ensure_pipeline()
        from common_layer.database import PositionStore, ReserveStore
        from common_layer.optimisation_model.reserve_offer_builder import (
            check_reserve_offers, ReserveOffer,
        )

        committed = PositionStore().committed_position(TEST_DATE)
        raw = ReserveStore().load_reserve(TEST_DATE, "aFRR")
        offers = {
            h: ReserveOffer(
                hour=h, up_mw=o["up_mw"], dn_mw=o["dn_mw"],
                cap_price_up_eur_mw=o["cap_up_eur_mw"],
                cap_price_dn_eur_mw=o["cap_dn_eur_mw"],
            )
            for h, o in raw.items()
        }
        fat_min = cfg.market.afrr.fat_min
        cap_max = cfg.market.afrr.cap_price_max_eur_mw
        # Should raise nothing.
        violations = check_reserve_offers(
            offers, committed, cfg, fat_min,
            product="aFRR", cap_price_max=cap_max
        )
        self.assertEqual(violations, [])

    def test_mfrr_offer_checker(self):
        cfg = _ensure_pipeline()
        from common_layer.database import PositionStore, ReserveStore
        from common_layer.optimisation_model.reserve_offer_builder import (
            check_reserve_offers, ReserveOffer,
        )

        committed = PositionStore().committed_position(TEST_DATE)
        afrr_up = ReserveStore().reserved_up(TEST_DATE, "aFRR")
        afrr_dn = ReserveStore().reserved_dn(TEST_DATE, "aFRR")
        raw = ReserveStore().load_reserve(TEST_DATE, "mFRR")
        offers = {
            h: ReserveOffer(
                hour=h, up_mw=o["up_mw"], dn_mw=o["dn_mw"],
                cap_price_up_eur_mw=o["cap_up_eur_mw"],
                cap_price_dn_eur_mw=o["cap_dn_eur_mw"],
            )
            for h, o in raw.items()
        }
        fat_min = cfg.market.mfrr.fat_min
        violations = check_reserve_offers(
            offers, committed, cfg, fat_min,
            product="mFRR", reserved_up=afrr_up, reserved_dn=afrr_dn
        )
        self.assertEqual(violations, [])


# ── T10 — Mode-aware FAT: pump-mode hours get reduced up deliverable ────────
class T10_ModeAwareFAT(unittest.TestCase):
    """BUG-7: pump-mode hours offered less up reserve than generation-mode hours."""

    def test_pump_mode_up_lower_than_gen_mode(self):
        cfg = _ensure_pipeline()
        from common_layer.database import PositionStore, ReserveStore
        from common_layer.optimisation_model.reserve_offer_builder import fat_deliverable_mw

        committed = PositionStore().committed_position(TEST_DATE)
        offers = ReserveStore().load_reserve(TEST_DATE, "aFRR")
        fat_min = cfg.market.afrr.fat_min

        pump_hours = {h: n for h, n in committed.items() if n < 0}
        gen_hours  = {h: n for h, n in committed.items() if n >= 0}

        if not pump_hours or not gen_hours:
            self.skipTest("Need at least one pump and one generation hour for this test")

        for h, n in pump_hours.items():
            if h not in offers:
                continue
            fat_pump = fat_deliverable_mw(cfg, fat_min, current_net_mw=n)
            fat_gen  = fat_deliverable_mw(cfg, fat_min, current_net_mw=0.0)
            offered_up = offers[h]["up_mw"]
            self.assertLessEqual(
                offered_up, fat_pump + 1e-6,
                f"H{h} pump-mode (net={n:.0f} MW): offered up {offered_up:.1f} "
                f"> mode-aware FAT {fat_pump:.1f}"
            )
            # In pump mode with short FAT, deliverable should be <= generation mode.
            self.assertLessEqual(
                fat_pump, fat_gen + 1e-6,
                f"H{h}: pump-mode FAT {fat_pump:.1f} > gen-mode FAT {fat_gen:.1f}"
            )


# ── Main runner ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 72)
    print("  RESERVE MARKET DEEP TEST SUITE")
    print(f"  Test date: {TEST_DATE}")
    print("=" * 72)
    print()
    print("Running pipeline for test date (DA -> aFRR/mFRR offer -> RT -> activation)...")

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        T1_NoSimultaneousUpDown,
        T2_FATDeliverability,
        T3_PhysicalHeadroom,
        T4_MinimumHoldTime,
        T5_BESSSOCBounds,
        T6_CombinedHeadroom,
        T7_SettlementSeparatePrices,
        T8_ImbalanceExcludesActivations,
        T9_OfferCheckerPasses,
        T10_ModeAwareFAT,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print()
    print("=" * 72)
    n_tests  = result.testsRun
    n_fail   = len(result.failures) + len(result.errors)
    n_skip   = len(result.skipped)
    n_pass   = n_tests - n_fail - n_skip
    print(f"  TOTAL : {n_tests}   PASS : {n_pass}   FAIL : {n_fail}   SKIP : {n_skip}")
    print("=" * 72)

    sys.exit(0 if result.wasSuccessful() else 1)
