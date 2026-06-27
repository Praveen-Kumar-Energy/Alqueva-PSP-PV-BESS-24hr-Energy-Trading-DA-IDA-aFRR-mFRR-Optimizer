"""
run_mfrr.py — Phase 3B mFRR capacity-offer gate.

mFRR = manual Frequency Restoration Reserve (tertiary control, supports/replaces
aFRR on TSO instruction).
  FAT : 12.5 minutes (MARI standard; REN on MARI since 27 Nov 2024)
  Sizing : from headroom REMAINING after the aFRR commitment (PR-11), x 0.20 margin

Run DA/IDA and then aFRR first so both an energy position and the aFRR commitment
exist.

    python phase_3b_mfrr_manual_frequency_reserve/run_mfrr.py --date 2026-06-26
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config, AppConfig
from common_layer.utilities import get_logger, AuditLogger
from common_layer.database import PositionStore, ReserveStore
from phase_3b_mfrr_manual_frequency_reserve.mfrr_price_forecasting.mari_mfrr_price_loader import (
    fetch_mfrr_cap_prices,
)
from phase_3b_mfrr_manual_frequency_reserve.mfrr_reserve_offer_builder.mfrr_offer_builder import (
    build_mfrr_offers,
)
from phase_3b_mfrr_manual_frequency_reserve.mfrr_reserve_offer_builder.mfrr_offer_checker import (
    check_mfrr_offers,
)
from common_layer.optimisation_model.reserve_offer_builder import ReserveCheckError

log = get_logger("phase3.mfrr")


def run_mfrr(delivery_date: str, cfg: AppConfig, no_pause: bool = False,
             use_synthetic: bool = True) -> dict:
    audit = AuditLogger()
    audit.log("MFRR_START", delivery_date=delivery_date)

    # REN joined MARI on mari_live_date; skip mFRR for earlier dates.
    if delivery_date < cfg.market.mfrr.mari_live_date:
        msg = (f"[mFRR] delivery {delivery_date} is before MARI live date "
               f"{cfg.market.mfrr.mari_live_date} — mFRR not available, skipping.")
        log.info(msg)
        audit.log("MFRR_SKIPPED", reason="pre-MARI")
        return {"status": "SKIPPED", "reason": msg, "capacity_revenue_eur": 0.0}

    committed = PositionStore().committed_position(delivery_date)
    if not committed:
        msg = "[mFRR] no committed energy position; run DA (and IDAs) first."
        log.error(msg)
        return {"status": "NO_BASELINE", "reason": msg}

    rstore = ReserveStore()
    reserved_up = rstore.reserved_up(delivery_date, "aFRR")
    reserved_dn = rstore.reserved_dn(delivery_date, "aFRR")
    if not reserved_up:
        log.warning("[mFRR] no aFRR commitment found — sizing from full headroom. "
                    "Run aFRR first for correct priority allocation.")

    hours = sorted(committed)
    cap_up, cap_dn, source = fetch_mfrr_cap_prices(hours, delivery_date, cfg, use_synthetic)
    offers = build_mfrr_offers(committed, cap_up, cap_dn, reserved_up, reserved_dn, cfg)

    try:
        check_mfrr_offers(offers, committed, reserved_up, reserved_dn, cfg)
    except ReserveCheckError as e:
        log.error(str(e))
        audit.log("MFRR_CHECK_FAILED", reason=str(e))
        return {"status": "CHECK_FAILED", "reason": str(e)}
    audit.log("MFRR_CHECK_PASSED")

    revenue = sum(o.up_mw * o.cap_price_up_eur_mw + o.dn_mw * o.cap_price_dn_eur_mw
                  for o in offers.values())
    _print_offers(cfg, source, offers, committed, reserved_up, reserved_dn, revenue)
    if not no_pause:
        try:
            input(f"\n  mFRR: expected capacity revenue {revenue:,.0f} EUR — "
                  f"submit offer?  [ENTER to continue] ")
        except (EOFError, KeyboardInterrupt):
            pass

    rstore.save_reserve(delivery_date, "mFRR", {
        h: {"up_mw": o.up_mw, "dn_mw": o.dn_mw,
            "cap_up_eur_mw": o.cap_price_up_eur_mw, "cap_dn_eur_mw": o.cap_price_dn_eur_mw}
        for h, o in offers.items()})
    ref = f"MFRR-{delivery_date.replace('-', '')}-001"
    audit.log("MFRR_SUBMITTED", ref=ref, capacity_revenue_eur=revenue, n_hours=len(offers))
    log.info(f"mFRR offer saved (stub submit) ref {ref}; capacity revenue {revenue:,.2f} EUR")
    return {"status": "SUBMITTED", "ref": ref, "capacity_revenue_eur": revenue,
            "price_source": source}


def _print_offers(cfg, source, offers, committed, reserved_up, reserved_dn, revenue):
    freq = cfg.market.frequency
    print("\n" + "=" * 68)
    print("  mFRR CAPACITY OFFER  (manual Frequency Restoration Reserve)")
    print("=" * 68)
    print(f"  FAT    : {cfg.market.mfrr.fat_min:.1f} min   |   Platform: MARI")
    print(f"  Sizing : <= {cfg.market.mfrr.max_offer_fraction:.0%} of headroom AFTER aFRR")
    print(f"  Source : {source}")
    print(f"\n  {'Hour':<5} {'Energy MW':>10} {'aFRRup':>7} {'mFRRup':>7} "
          f"{'mFRRdn':>7} {'CapUp':>7}")
    print("  " + "-" * 52)
    for h in sorted(offers):
        o = offers[h]
        print(f"  H{h:02d}  {committed.get(h,0.0):>+10.1f} {reserved_up.get(h,0.0):>7.1f} "
              f"{o.up_mw:>7.1f} {o.dn_mw:>7.1f} {o.cap_price_up_eur_mw:>7.1f}")
    print("  " + "-" * 52)
    print(f"  Expected mFRR capacity revenue: {revenue:>12,.2f} EUR")
    print("=" * 68)


def main():
    p = argparse.ArgumentParser(description="Run the Phase 3B mFRR offer gate")
    p.add_argument("--date", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--no-pause", action="store_true")
    args = p.parse_args()
    result = run_mfrr(args.date, load_config(args.config), no_pause=args.no_pause)
    print("\n  RESULT:", result.get("status"))
    for k, v in result.items():
        if k != "status":
            print(f"    {k}: {v}")
    sys.exit(0 if result.get("status") == "SUBMITTED" else 1)


if __name__ == "__main__":
    main()
