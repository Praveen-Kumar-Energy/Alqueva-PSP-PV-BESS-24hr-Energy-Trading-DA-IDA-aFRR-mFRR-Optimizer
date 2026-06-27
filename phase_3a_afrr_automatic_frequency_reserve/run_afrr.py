"""
run_afrr.py — Phase 3A aFRR capacity-offer gate.

aFRR = automatic Frequency Restoration Reserve (secondary control).
  Band : restores frequency within +/- 0.200 Hz (49.800 - 50.200 Hz)
  FAT  : 5 minutes (PICASSO harmonised, since 4 Dec 2024)
  Cap  : <= 250 EUR/MW availability price (REN)

Offers the headroom left after the committed energy position (PR-11: no MW sold
twice), bounded by FAT deliverability and the market max. Run the energy gates
(DA / IDA) first so a committed position exists.

    python phase_3a_afrr_automatic_frequency_reserve/run_afrr.py --date 2026-06-26
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config, AppConfig
from common_layer.utilities import get_logger, AuditLogger
from common_layer.utilities import date_utils as du
from common_layer.database import PositionStore, ReserveStore
from phase_3a_afrr_automatic_frequency_reserve.afrr_price_forecasting.picasso_afrr_price_loader import (
    fetch_afrr_cap_prices,
)
from phase_3a_afrr_automatic_frequency_reserve.afrr_reserve_offer_builder.afrr_offer_builder import (
    build_afrr_offers,
)
from phase_3a_afrr_automatic_frequency_reserve.afrr_reserve_offer_builder.afrr_offer_checker import (
    check_afrr_offers,
)
from common_layer.optimisation_model.reserve_offer_builder import ReserveCheckError

log = get_logger("phase3.afrr")


def run_afrr(delivery_date: str, cfg: AppConfig, no_pause: bool = False,
             use_synthetic: bool = True) -> dict:
    audit = AuditLogger()
    audit.log("AFRR_START", delivery_date=delivery_date)

    committed = PositionStore().committed_position(delivery_date)
    if not committed:
        msg = "[aFRR] no committed energy position; run DA (and IDAs) first."
        log.error(msg)
        return {"status": "NO_BASELINE", "reason": msg}

    hours = sorted(committed)
    cap_up, cap_dn, source = fetch_afrr_cap_prices(hours, delivery_date, cfg, use_synthetic)
    offers = build_afrr_offers(committed, cap_up, cap_dn, cfg)

    try:
        check_afrr_offers(offers, committed, cfg)
    except ReserveCheckError as e:
        log.error(str(e))
        audit.log("AFRR_CHECK_FAILED", reason=str(e))
        return {"status": "CHECK_FAILED", "reason": str(e)}
    audit.log("AFRR_CHECK_PASSED")

    revenue = sum(o.up_mw * o.cap_price_up_eur_mw + o.dn_mw * o.cap_price_dn_eur_mw
                  for o in offers.values())

    _print_offers(cfg, source, offers, committed, revenue)
    if not no_pause:
        try:
            input(f"\n  aFRR: expected capacity revenue {revenue:,.0f} EUR — "
                  f"submit offer?  [ENTER to continue] ")
        except (EOFError, KeyboardInterrupt):
            pass

    ReserveStore().save_reserve(delivery_date, "aFRR", {
        h: {"up_mw": o.up_mw, "dn_mw": o.dn_mw,
            "cap_up_eur_mw": o.cap_price_up_eur_mw, "cap_dn_eur_mw": o.cap_price_dn_eur_mw}
        for h, o in offers.items()})
    ref = f"AFRR-{delivery_date.replace('-', '')}-001"
    audit.log("AFRR_SUBMITTED", ref=ref, capacity_revenue_eur=revenue, n_hours=len(offers))
    log.info(f"aFRR offer saved (stub submit) ref {ref}; capacity revenue {revenue:,.2f} EUR")
    return {"status": "SUBMITTED", "ref": ref, "capacity_revenue_eur": revenue,
            "price_source": source}


def _print_offers(cfg, source, offers, committed, revenue):
    freq = cfg.market.frequency
    print("\n" + "=" * 64)
    print("  aFRR CAPACITY OFFER  (automatic Frequency Restoration Reserve)")
    print("=" * 64)
    print(f"  Band   : {freq.nominal_hz - freq.afrr_band_hz:.3f} - "
          f"{freq.nominal_hz + freq.afrr_band_hz:.3f} Hz   "
          f"(nominal {freq.nominal_hz:.3f} Hz)")
    print(f"  FAT    : {cfg.market.afrr.fat_min:.0f} min   |   Platform: {cfg.market.afrr.platform}")
    print(f"  Source : {source}   |   Cap ceiling: {cfg.market.afrr.cap_price_max_eur_mw:.0f} EUR/MW")
    print(f"\n  {'Hour':<5} {'Energy MW':>10} {'Up MW':>8} {'Dn MW':>8} "
          f"{'CapUp':>7} {'CapDn':>7}")
    print("  " + "-" * 52)
    for h in sorted(offers):
        o = offers[h]
        print(f"  H{h:02d}  {committed.get(h,0.0):>+10.1f} {o.up_mw:>8.1f} {o.dn_mw:>8.1f} "
              f"{o.cap_price_up_eur_mw:>7.1f} {o.cap_price_dn_eur_mw:>7.1f}")
    print("  " + "-" * 52)
    print(f"  Expected aFRR capacity revenue: {revenue:>12,.2f} EUR")
    print("=" * 64)


def main():
    p = argparse.ArgumentParser(description="Run the Phase 3A aFRR offer gate")
    p.add_argument("--date", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--no-pause", action="store_true")
    args = p.parse_args()
    result = run_afrr(args.date, load_config(args.config), no_pause=args.no_pause)
    print("\n  RESULT:", result.get("status"))
    for k, v in result.items():
        if k != "status":
            print(f"    {k}: {v}")
    sys.exit(0 if result.get("status") == "SUBMITTED" else 1)


if __name__ == "__main__":
    main()
