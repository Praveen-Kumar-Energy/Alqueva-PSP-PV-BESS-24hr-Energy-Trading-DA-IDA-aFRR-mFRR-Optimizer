"""
run_reserve_settlement.py — Phase 5B reserve (aFRR + mFRR) settlement.

Settles both reserve products in two components: capacity (availability payment
per offered MW per hour) and activation (energy payment per activated ISP).
Uses the isp_duration from date_utils so aFRR and mFRR eff_isp_h are consistent
with Phase 4B/4C logged values.

    python phase_5b_reserve_settlement/run_reserve_settlement.py --date 2026-06-26
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config
from common_layer.utilities import get_logger, AuditLogger
from common_layer.utilities import date_utils as du
from phase_5b_reserve_settlement.reserve_settlement_calculation.afrr_settlement_calculator import (
    settle_afrr,
)
from phase_5b_reserve_settlement.reserve_settlement_calculation.mfrr_settlement_calculator import (
    settle_mfrr,
)

log = get_logger("phase5.reserve")


def run_reserve_settlement(delivery_date: str, config_dir=None) -> dict:
    cfg = load_config(config_dir)
    audit = AuditLogger()
    day = du.parse_date(delivery_date)
    isp_h = du.isp_duration_min(day) / 60.0

    afrr = settle_afrr(delivery_date, isp_h)
    mfrr = settle_mfrr(delivery_date, isp_h)
    total = afrr.total_eur + mfrr.total_eur

    print("\n" + "=" * 60)
    print(f"  RESERVE SETTLEMENT  —  {delivery_date}")
    print("=" * 60)
    print(f"  {'Product':<8} {'Capacity EUR':>16} {'Activation EUR':>16} {'Total EUR':>14}")
    print("  " + "-" * 56)
    for s in (afrr, mfrr):
        print(f"  {s.product:<8} {s.capacity_eur:>16,.2f} {s.activation_eur:>16,.2f} "
              f"{s.total_eur:>14,.2f}")
    print("  " + "-" * 56)
    print(f"  {'TOTAL':<8} {'':>16} {'':>16} {total:>14,.2f}")
    print("=" * 60)

    audit.log("RESERVE_SETTLED", afrr_eur=afrr.total_eur, mfrr_eur=mfrr.total_eur, total_eur=total)
    return {"status": "OK",
            "afrr_capacity_eur": afrr.capacity_eur, "afrr_activation_eur": afrr.activation_eur,
            "mfrr_capacity_eur": mfrr.capacity_eur, "mfrr_activation_eur": mfrr.activation_eur,
            "total_reserve_eur": total}


def main():
    p = argparse.ArgumentParser(description="Run Phase 5B reserve settlement")
    p.add_argument("--date", required=True)
    p.add_argument("--config", default=None)
    args = p.parse_args()
    r = run_reserve_settlement(args.date, args.config)
    print("\n  RESULT:", r.get("status"))
    sys.exit(0)


if __name__ == "__main__":
    main()
