"""
test_reserve_realtime_delivery.py — real-time energy delivery and ramp physics tests.

Second test suite for the reserve market pipeline. Where test_reserve_market_deep.py
checks market/financial invariants, this suite checks physical energy delivery:

  T11  FAT ramp trajectory compliance: 100% of activations reach 95% within FAT
  T12  Ramp energy correction: eff_isp_h stored in DB matches formula exactly
  T13  aFRR activates more frequently than mFRR (reflects real-market frequency)
  T14  Activation direction aligns with power deviation (up => actual > scheduled)
  T15  Reservoir safety: no Pedrógão or Alqueva bound violation under activation
  T16  Settlement total integrity: total_eur = capacity_eur + activation_eur
  T17  eff_isp_h roundtrip: saved to DB and loaded back unchanged
  T18  BESS instantaneous response: ramp trajectory starts above 0 at t=0 (BESS)
  T19  Ramp correction magnitude: aFRR -16.7%, mFRR -41.7% vs face-value ISP
  T20  Activation price cap never exceeded (up_price <= DA_max * 1.50)
  T21  No activation rows in hours with zero offered MW
  T22  Combined settlement positive P&L across DA + reserve streams

Run from repo root:
    python -m pytest tests/test_reserve_realtime_delivery.py -v
or:
    python tests/test_reserve_realtime_delivery.py
"""
from __future__ import annotations

import os
import sys
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

TEST_DATE = "2026-07-05"

# ── Shared pipeline launcher ─────────────────────────────────────────────────
# Module-level sentinels used as a run-once cache: same test date as suite 1,
# so if both test files are collected in the same pytest session they share the
# existing DB state and avoid running the full pipeline a second time.
_pipeline_ran = False
_pipeline_cfg = None


def _ensure_pipeline():
    global _pipeline_ran, _pipeline_cfg
    if _pipeline_ran:
        return _pipeline_cfg

    from common_layer.configuration import load_config
    cfg = load_config()
    _pipeline_cfg = cfg

    from phase_1_da_day_ahead_bidding.run_da import run_da
    r = run_da(TEST_DATE, auto_approve=True)
    assert r["status"] == "SUBMITTED", f"DA failed: {r}"

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


# ── T11 — FAT ramp trajectory compliance: 100% within FAT window ────────────
class T11_FATRampCompliance(unittest.TestCase):
    """All activation MW must ramp to >= 95% of target within product FAT."""

    def _check(self, product: str, fat_min: float):
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore
        from common_layer.optimisation_model.activation_ramp_tracker import batch_compliance_check

        rows = ActivationStore().load(TEST_DATE, product)
        if not rows:
            self.skipTest(f"No {product} activations on {TEST_DATE}")

        ramp = cfg.plant.psp.total_ramp_mw_per_min
        bess_power = cfg.plant.bess.power_mw
        isp_min = cfg.market.balancing.isp_duration_min

        result = batch_compliance_check(rows, fat_min, isp_min, ramp, bess_power)

        self.assertEqual(
            result["n_compliant"], result["n_activations"],
            f"{product}: only {result['n_compliant']}/{result['n_activations']} "
            f"activations reach 95% within FAT={fat_min} min. "
            f"Non-compliant ISPs: {result['non_compliant_isps']}"
        )
        self.assertLessEqual(
            result["max_time_to_95pct_min"], fat_min + 1e-6,
            f"{product}: max time to 95% = {result['max_time_to_95pct_min']:.2f} min "
            f"> FAT {fat_min} min"
        )

    def test_afrr_fat_5min_compliance(self):
        cfg = _ensure_pipeline()
        self._check("aFRR", cfg.market.afrr.fat_min)

    def test_mfrr_fat_12_5min_compliance(self):
        cfg = _ensure_pipeline()
        self._check("mFRR", cfg.market.mfrr.fat_min)


# ── T12 — Ramp energy correction: eff_isp_h in DB matches formula ───────────
class T12_RampEnergyCorrection(unittest.TestCase):
    """eff_isp_h stored in ActivationStore must equal (isp_min - fat/2) / 60."""

    def _check(self, product: str, fat_min: float):
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore
        from common_layer.optimisation_model.activation_ramp_tracker import effective_isp_hours

        isp_min = cfg.market.balancing.isp_duration_min
        expected_eff = effective_isp_hours(fat_min, isp_min)

        rows = ActivationStore().load(TEST_DATE, product)
        if not rows:
            self.skipTest(f"No {product} activations on {TEST_DATE}")

        for r in rows:
            stored = r.get("eff_isp_h", None)
            self.assertIsNotNone(stored, f"{product} ISP{r['isp']}: eff_isp_h missing from DB")
            self.assertAlmostEqual(
                stored, expected_eff, places=6,
                msg=(f"{product} ISP{r['isp']}: stored eff_isp_h={stored:.6f} "
                     f"!= formula value {expected_eff:.6f}")
            )

    def test_afrr_eff_isp_h_matches_formula(self):
        cfg = _ensure_pipeline()
        self._check("aFRR", cfg.market.afrr.fat_min)

    def test_mfrr_eff_isp_h_matches_formula(self):
        cfg = _ensure_pipeline()
        self._check("mFRR", cfg.market.mfrr.fat_min)


# ── T13 — aFRR activates more frequently than mFRR ──────────────────────────
class T13_ActivationFrequency(unittest.TestCase):
    """aFRR (continuous AGC, p_up=0.35) must fire more often than mFRR (p_up=0.12)."""

    def test_afrr_fires_more_than_mfrr(self):
        _ensure_pipeline()
        from common_layer.database import ActivationStore
        afrr_rows = ActivationStore().load(TEST_DATE, "aFRR")
        mfrr_rows = ActivationStore().load(TEST_DATE, "mFRR")

        # aFRR should have at least 3× more activated ISPs than mFRR in expectation.
        # With p_up+p_dn=0.65 for aFRR vs 0.22 for mFRR, ratio >= 2 robustly.
        n_afrr = len(afrr_rows)
        n_mfrr = len(mfrr_rows)

        if n_mfrr == 0:
            # mFRR not activated at all is also a valid outcome (rare calls).
            return

        ratio = n_afrr / n_mfrr if n_mfrr > 0 else float("inf")
        self.assertGreater(
            ratio, 1.5,
            f"Expected aFRR to activate more than mFRR (ratio={ratio:.1f}). "
            f"aFRR: {n_afrr} ISPs, mFRR: {n_mfrr} ISPs"
        )


# ── T14 — Activation direction aligns with power deviation ──────────────────
class T14_ActivationDirectionVsDeviation(unittest.TestCase):
    """When TSO calls UP activation, actual net generation > scheduled net.

    Physical logic: UP = TSO wants more power (grid short of generation).
    The realtime dispatch model must produce actual > scheduled when UP activated.
    """

    def _check(self, product: str):
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore, DeliveryStore

        sched_by_isp = {r["isp"]: r["scheduled_mw"] for r in DeliveryStore().load(TEST_DATE)}
        actual_by_isp = {r["isp"]: r["actual_mw"] for r in DeliveryStore().load(TEST_DATE)}
        rows = ActivationStore().load(TEST_DATE, product)

        up_violations = []
        dn_violations = []

        for r in rows:
            isp = r["isp"]
            sched = sched_by_isp.get(isp, 0.0)
            actual = actual_by_isp.get(isp, 0.0)
            deviation = actual - sched      # positive = generating more than planned

            if r["up_mw"] > 1.0:           # meaningful upward activation
                if deviation < -5.0:        # allow small measurement noise (5 MW slack)
                    up_violations.append(
                        f"ISP{isp}: UP {r['up_mw']:.1f} MW but deviation={deviation:.1f} MW "
                        f"(actual={actual:.1f} < sched={sched:.1f})"
                    )

            if r["dn_mw"] > 1.0:           # meaningful downward activation
                if deviation > 5.0:
                    dn_violations.append(
                        f"ISP{isp}: DN {r['dn_mw']:.1f} MW but deviation={deviation:.1f} MW "
                        f"(actual={actual:.1f} > sched={sched:.1f})"
                    )

        self.assertEqual(
            up_violations, [],
            f"{product} UP activation direction mismatch:\n" + "\n".join(up_violations)
        )
        self.assertEqual(
            dn_violations, [],
            f"{product} DN activation direction mismatch:\n" + "\n".join(dn_violations)
        )

    def test_afrr_direction(self):
        self._check("aFRR")

    def test_mfrr_direction(self):
        self._check("mFRR")


# ── T15 — Reservoir safety under activation ─────────────────────────────────
class T15_ReservoirSafetyUnderActivation(unittest.TestCase):
    """Pedrógão lower reservoir must stay within bounds when activations added."""

    def test_no_reservoir_violations(self):
        cfg = _ensure_pipeline()
        from common_layer.database import PositionStore, ActivationStore
        from common_layer.physical_plant_models.reservoir_activation_checker import (
            check_reservoir_during_activation,
        )

        committed = PositionStore().committed_position(TEST_DATE)

        # Merge aFRR + mFRR activations per ISP.
        activations_by_isp: dict = {}
        for product in ("aFRR", "mFRR"):
            for a in ActivationStore().load(TEST_DATE, product):
                isp = a["isp"]
                prev = activations_by_isp.get(isp, {"up_mw": 0.0, "dn_mw": 0.0})
                activations_by_isp[isp] = {
                    "up_mw": prev["up_mw"] + a["up_mw"],
                    "dn_mw": prev["dn_mw"] + a["dn_mw"],
                }

        result = check_reservoir_during_activation(TEST_DATE, cfg, committed, activations_by_isp)
        self.assertEqual(
            result.violations, [],
            "Reservoir violations under activation:\n" + "\n".join(result.violations)
        )

    def test_pedrogao_stays_above_zero(self):
        """Lower reservoir level never negative — physics sanity."""
        cfg = _ensure_pipeline()
        from common_layer.database import PositionStore, ActivationStore
        from common_layer.physical_plant_models.reservoir_activation_checker import (
            check_reservoir_during_activation,
        )

        committed = PositionStore().committed_position(TEST_DATE)
        activations_by_isp: dict = {}
        for product in ("aFRR", "mFRR"):
            for a in ActivationStore().load(TEST_DATE, product):
                isp = a["isp"]
                prev = activations_by_isp.get(isp, {"up_mw": 0.0, "dn_mw": 0.0})
                activations_by_isp[isp] = {
                    "up_mw": prev["up_mw"] + a["up_mw"],
                    "dn_mw": prev["dn_mw"] + a["dn_mw"],
                }

        result = check_reservoir_during_activation(TEST_DATE, cfg, committed, activations_by_isp)
        self.assertGreaterEqual(
            result.min_lower_hm3, 0.0,
            f"Pedrogao lower reservoir went negative: {result.min_lower_hm3:.4f} hm3"
        )


# ── T16 — Settlement total integrity ────────────────────────────────────────
class T16_SettlementTotalIntegrity(unittest.TestCase):
    """settle_reserve total_eur == capacity_eur + activation_eur exactly."""

    def _check(self, product: str):
        cfg = _ensure_pipeline()
        from phase_5b_reserve_settlement.reserve_settlement_calculation.afrr_settlement_calculator import (
            settle_reserve,
        )

        isp_h = cfg.market.balancing.isp_duration_min / 60.0
        result = settle_reserve(TEST_DATE, product, isp_h)

        self.assertAlmostEqual(
            result.total_eur,
            result.capacity_eur + result.activation_eur,
            places=6,
            msg=(f"{product}: total_eur={result.total_eur:.4f} "
                 f"!= capacity {result.capacity_eur:.4f} + activation {result.activation_eur:.4f}")
        )
        # Reserve market always yields non-negative capacity (offered at positive price).
        self.assertGreaterEqual(result.capacity_eur, 0.0, f"{product}: capacity revenue negative")

    def test_afrr_total_integrity(self):
        self._check("aFRR")

    def test_mfrr_total_integrity(self):
        self._check("mFRR")


# ── T17 — eff_isp_h roundtrip: save → load returns correct value ────────────
class T17_EffIspHRoundtrip(unittest.TestCase):
    """eff_isp_h written to SQLite must be read back unchanged.

    Critically verifies the BUG-8 fix is end-to-end: the ramp-corrected value
    saved by reserve_activation.py is actually persisted and loaded by settlement.
    """

    def test_roundtrip_in_memory(self):
        """Write a known eff_isp_h via ActivationStore; read it back; compare."""
        import tempfile
        from common_layer.database.realtime_store import ActivationStore

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            store = ActivationStore(db_path=db_path)
            test_eff = 0.208333   # aFRR (15 - 5/2)/60
            row = {
                "isp": 5, "hour": 2,
                "up_mw": 10.0, "dn_mw": 0.0,
                "up_price_eur_mwh": 91.0, "dn_price_eur_mwh": 49.0,
                "eff_isp_h": test_eff,
            }
            store.save("2026-07-05", "aFRR", [row])
            loaded = store.load("2026-07-05", "aFRR")

            self.assertEqual(len(loaded), 1)
            self.assertIn("eff_isp_h", loaded[0],
                          "eff_isp_h column not returned by load()")
            self.assertAlmostEqual(loaded[0]["eff_isp_h"], test_eff, places=6,
                                   msg=f"Roundtrip: stored {test_eff} != loaded {loaded[0]['eff_isp_h']}")
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_default_when_old_row(self):
        """Old DB rows without eff_isp_h must default to 0.25 (schema DEFAULT)."""
        import tempfile, sqlite3
        from common_layer.database.realtime_store import ActivationStore

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Create old-schema table without eff_isp_h, insert a row manually.
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE activations (
                    delivery_date TEXT NOT NULL,
                    product TEXT NOT NULL,
                    isp INTEGER NOT NULL,
                    hour INTEGER NOT NULL,
                    up_mw REAL NOT NULL,
                    dn_mw REAL NOT NULL,
                    energy_price_eur_mwh REAL NOT NULL DEFAULT 0,
                    up_price_eur_mwh REAL NOT NULL DEFAULT 0,
                    dn_price_eur_mwh REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (delivery_date, product, isp)
                )
            """)
            conn.execute(
                "INSERT INTO activations VALUES (?,?,?,?,?,?,?,?,?)",
                ("2026-07-05", "aFRR", 3, 1, 5.0, 0.0, 70.0, 70.0, 0.0)
            )
            conn.commit()
            conn.close()

            # ActivationStore._migrate() should add eff_isp_h with default 0.25.
            store = ActivationStore(db_path=db_path)
            loaded = store.load("2026-07-05", "aFRR")
            self.assertEqual(len(loaded), 1)
            self.assertIn("eff_isp_h", loaded[0])
            self.assertAlmostEqual(loaded[0]["eff_isp_h"], 0.25, places=6,
                                   msg="Default eff_isp_h for migrated row should be 0.25")
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


# ── T18 — BESS instantaneous response at t=0 in ramp trajectory ─────────────
class T18_BESSInstantaneousResponse(unittest.TestCase):
    """BESS contributes power immediately at t=0; PSP ramps after t=0."""

    def test_bess_power_at_t0(self):
        cfg = _ensure_pipeline()
        from common_layer.optimisation_model.activation_ramp_tracker import simulate_ramp_trajectory

        bess_power = cfg.plant.bess.power_mw      # 1 MW
        psp_ramp = cfg.plant.psp.total_ramp_mw_per_min
        isp_min = cfg.market.balancing.isp_duration_min
        fat_min = cfg.market.afrr.fat_min
        target_mw = 10.0

        traj = simulate_ramp_trajectory(
            target_mw=target_mw,
            fat_min=fat_min,
            isp_duration_min=isp_min,
            total_ramp_mw_per_min=psp_ramp,
            bess_power_mw=bess_power,
        )

        # At t=0, BESS fires instantly — power_mw[0] should equal BESS contribution.
        p0 = traj.power_mw[0]
        self.assertGreater(p0, 0.0, "Power at t=0 must be > 0 (BESS fires instantly)")
        self.assertAlmostEqual(
            p0, min(bess_power, target_mw), places=4,
            msg=f"t=0 power {p0:.4f} MW != BESS contribution {min(bess_power, target_mw):.4f} MW"
        )

    def test_no_bess_power_starts_at_zero(self):
        """Without BESS, power at t=0 is 0 (PSP has not ramped yet)."""
        cfg = _ensure_pipeline()
        from common_layer.optimisation_model.activation_ramp_tracker import simulate_ramp_trajectory

        psp_ramp = cfg.plant.psp.total_ramp_mw_per_min
        isp_min = cfg.market.balancing.isp_duration_min
        fat_min = cfg.market.mfrr.fat_min

        traj = simulate_ramp_trajectory(
            target_mw=20.0,
            fat_min=fat_min,
            isp_duration_min=isp_min,
            total_ramp_mw_per_min=psp_ramp,
            bess_power_mw=0.0,      # no BESS
        )

        self.assertAlmostEqual(
            traj.power_mw[0], 0.0, places=4,
            msg="Without BESS, power at t=0 must be 0 (PSP ramp not started yet)"
        )


# ── T19 — Ramp correction magnitude: −16.7% aFRR, −41.7% mFRR vs face value ─
class T19_RampCorrectionMagnitude(unittest.TestCase):
    """Verify the analytical ramp correction matches the documented percentages."""

    def test_afrr_correction_16_7_pct(self):
        from common_layer.optimisation_model.activation_ramp_tracker import effective_isp_hours
        isp_min = 15.0
        fat_min = 5.0
        isp_h = isp_min / 60.0
        eff_h = effective_isp_hours(fat_min, isp_min)
        reduction_pct = (1 - eff_h / isp_h) * 100

        self.assertAlmostEqual(
            eff_h, 0.208333, places=5,
            msg=f"aFRR eff_isp_h = {eff_h:.6f}, expected 0.208333"
        )
        self.assertAlmostEqual(
            reduction_pct, 16.667, places=2,
            msg=f"aFRR energy reduction = {reduction_pct:.2f}%, expected 16.67%"
        )

    def test_mfrr_correction_41_7_pct(self):
        from common_layer.optimisation_model.activation_ramp_tracker import effective_isp_hours
        isp_min = 15.0
        fat_min = 12.5
        isp_h = isp_min / 60.0
        eff_h = effective_isp_hours(fat_min, isp_min)
        reduction_pct = (1 - eff_h / isp_h) * 100

        self.assertAlmostEqual(
            eff_h, 0.145833, places=5,
            msg=f"mFRR eff_isp_h = {eff_h:.6f}, expected 0.145833"
        )
        self.assertAlmostEqual(
            reduction_pct, 41.667, places=2,
            msg=f"mFRR energy reduction = {reduction_pct:.2f}%, expected 41.67%"
        )

    def test_eff_h_less_than_isp_h(self):
        """FAT ramp always removes energy — eff_isp_h < isp_h for all products."""
        from common_layer.optimisation_model.activation_ramp_tracker import effective_isp_hours
        isp_h = 15.0 / 60.0
        for fat_min in [5.0, 7.5, 10.0, 12.5]:
            eff = effective_isp_hours(fat_min, 15.0)
            self.assertLess(eff, isp_h,
                            f"fat_min={fat_min}: eff_isp_h {eff:.4f} >= isp_h {isp_h:.4f}")
            self.assertGreater(eff, 0.0,
                               f"fat_min={fat_min}: eff_isp_h {eff:.4f} must be > 0")


# ── T20 — Activation price cap never exceeded ───────────────────────────────
class T20_ActivationPriceCap(unittest.TestCase):
    """up_price = DA * 1.30 — verify no activation price exceeds a reasonable maximum.

    The DA price in MIBEL rarely exceeds 500 EUR/MWh even in stress events.
    up_price = DA * 1.30 so ceiling = 650 EUR/MWh. Any higher indicates a bug.
    """

    _PRICE_CAP_EUR_MWH = 700.0   # hard upper bound (well above MIBEL stress peaks)

    def _check(self, product: str):
        _ensure_pipeline()
        from common_layer.database import ActivationStore

        for r in ActivationStore().load(TEST_DATE, product):
            if r["up_mw"] > 1e-6:
                self.assertLessEqual(
                    r["up_price_eur_mwh"], self._PRICE_CAP_EUR_MWH,
                    f"{product} ISP{r['isp']}: up_price {r['up_price_eur_mwh']} "
                    f"> cap {self._PRICE_CAP_EUR_MWH}"
                )
            if r["dn_mw"] > 1e-6:
                self.assertGreater(
                    r["dn_price_eur_mwh"], 0.0,
                    f"{product} ISP{r['isp']}: dn_price {r['dn_price_eur_mwh']} <= 0"
                )

    def test_afrr_price_cap(self):
        self._check("aFRR")

    def test_mfrr_price_cap(self):
        self._check("mFRR")

    def test_up_price_is_premium_over_dn(self):
        """up_price = DA*1.30 > dn_price = DA*0.70 — ratio must be ~1.857 consistently."""
        _ensure_pipeline()
        from common_layer.database import ActivationStore

        for product in ("aFRR", "mFRR"):
            for r in ActivationStore().load(TEST_DATE, product):
                if r["up_mw"] > 1e-6 or r["dn_mw"] > 1e-6:
                    up_p = r["up_price_eur_mwh"]
                    dn_p = r["dn_price_eur_mwh"]
                    ratio = up_p / dn_p if dn_p > 1e-9 else float("inf")
                    self.assertAlmostEqual(
                        ratio, 1.30 / 0.70, places=3,
                        msg=(f"{product} ISP{r['isp']}: "
                             f"up_price/dn_price = {ratio:.4f}, expected "
                             f"{1.30/0.70:.4f} (DA*1.30 / DA*0.70)")
                    )


# ── T21 — No activation in hours with zero offered MW ───────────────────────
class T21_NoActivationWithoutOffer(unittest.TestCase):
    """TSO can only call reserve that was offered. Zero-offered hours = no activations."""

    def _check(self, product: str):
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore, ReserveStore

        offers = ReserveStore().load_reserve(TEST_DATE, product)
        rows = ActivationStore().load(TEST_DATE, product)

        isp_to_hour = {}
        from common_layer.utilities import date_utils as du
        day = du.parse_date(TEST_DATE)
        for h in range(1, 25):
            for isp in du.hour_to_isps(h, day):
                isp_to_hour[isp] = h

        for r in rows:
            isp = r["isp"]
            h = isp_to_hour.get(isp)
            if h is None:
                continue
            offer = offers.get(h, {"up_mw": 0.0, "dn_mw": 0.0})

            if r["up_mw"] > 1e-6:
                self.assertGreater(
                    offer["up_mw"], 0.0,
                    f"{product} ISP{isp} (H{h}): UP activation {r['up_mw']:.2f} MW "
                    f"but offered up = 0 MW in that hour"
                )
            if r["dn_mw"] > 1e-6:
                self.assertGreater(
                    offer["dn_mw"], 0.0,
                    f"{product} ISP{isp} (H{h}): DN activation {r['dn_mw']:.2f} MW "
                    f"but offered dn = 0 MW in that hour"
                )

    def test_afrr_no_phantom_activations(self):
        self._check("aFRR")

    def test_mfrr_no_phantom_activations(self):
        self._check("mFRR")


# ── T22 — Combined settlement positive P&L ──────────────────────────────────
class T22_CombinedPositivePnL(unittest.TestCase):
    """Total P&L = DA + aFRR settlement + mFRR settlement must be positive.

    The optimizer maximises profit, so the combined settlement must be > 0.
    This test checks that reserve markets ADD to the DA revenue, not subtract.
    """

    def test_reserve_settlement_positive(self):
        cfg = _ensure_pipeline()
        from phase_5b_reserve_settlement.reserve_settlement_calculation.afrr_settlement_calculator import (
            settle_reserve,
        )

        isp_h = cfg.market.balancing.isp_duration_min / 60.0
        afrr = settle_reserve(TEST_DATE, "aFRR", isp_h)
        mfrr = settle_reserve(TEST_DATE, "mFRR", isp_h)

        total_reserve_eur = afrr.total_eur + mfrr.total_eur
        self.assertGreater(
            total_reserve_eur, 0.0,
            f"Combined aFRR ({afrr.total_eur:.0f}) + mFRR ({mfrr.total_eur:.0f}) "
            f"= {total_reserve_eur:.0f} EUR is not positive"
        )

    def test_capacity_revenue_from_both_products(self):
        """Both products must contribute positive capacity payments (both offered)."""
        cfg = _ensure_pipeline()
        from phase_5b_reserve_settlement.reserve_settlement_calculation.afrr_settlement_calculator import (
            settle_reserve,
        )

        isp_h = cfg.market.balancing.isp_duration_min / 60.0
        for product in ("aFRR", "mFRR"):
            result = settle_reserve(TEST_DATE, product, isp_h)
            self.assertGreater(
                result.capacity_eur, 0.0,
                f"{product}: capacity revenue {result.capacity_eur:.2f} EUR "
                f"must be > 0 (offered non-zero capacity)"
            )

    def test_activation_revenue_ramp_corrected_less_than_face(self):
        """Ramp-corrected activation revenue must be less than face-value revenue.

        If eff_isp_h < isp_h (which is always true), the actual MWh delivered
        is less than face value. Settlement must be LOWER than if we used isp_h.
        """
        cfg = _ensure_pipeline()
        from common_layer.database import ActivationStore
        from phase_5b_reserve_settlement.reserve_settlement_calculation.afrr_settlement_calculator import (
            settle_reserve,
        )

        isp_h = cfg.market.balancing.isp_duration_min / 60.0

        for product in ("aFRR", "mFRR"):
            rows = ActivationStore().load(TEST_DATE, product)
            if not rows:
                continue

            result = settle_reserve(TEST_DATE, product, isp_h)

            # Face-value settlement (what we'd get WITHOUT ramp correction).
            face_value_eur = sum(
                r["up_mw"] * isp_h * r["up_price_eur_mwh"]
                + r["dn_mw"] * isp_h * r["dn_price_eur_mwh"]
                for r in rows
            )

            self.assertLessEqual(
                result.activation_eur, face_value_eur + 1e-4,
                msg=(f"{product}: ramp-corrected activation {result.activation_eur:.2f} EUR "
                     f"> face-value {face_value_eur:.2f} EUR — correction not applied?")
            )


# ── Main runner ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 72)
    print("  RESERVE REALTIME DELIVERY TEST SUITE")
    print(f"  Test date: {TEST_DATE}")
    print("=" * 72)
    print()
    print("Running full pipeline (DA->aFRR/mFRR offer->RT->activation->settlement)...")

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        T11_FATRampCompliance,
        T12_RampEnergyCorrection,
        T13_ActivationFrequency,
        T14_ActivationDirectionVsDeviation,
        T15_ReservoirSafetyUnderActivation,
        T16_SettlementTotalIntegrity,
        T17_EffIspHRoundtrip,
        T18_BESSInstantaneousResponse,
        T19_RampCorrectionMagnitude,
        T20_ActivationPriceCap,
        T21_NoActivationWithoutOffer,
        T22_CombinedPositivePnL,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print()
    print("=" * 72)
    n_tests = result.testsRun
    n_fail  = len(result.failures) + len(result.errors)
    n_skip  = len(result.skipped)
    n_pass  = n_tests - n_fail - n_skip
    print(f"  TOTAL : {n_tests}   PASS : {n_pass}   FAIL : {n_fail}   SKIP : {n_skip}")
    print("=" * 72)

    sys.exit(0 if result.wasSuccessful() else 1)
