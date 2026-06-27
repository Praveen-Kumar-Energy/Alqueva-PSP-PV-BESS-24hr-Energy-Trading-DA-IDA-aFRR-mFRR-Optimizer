"""
run_imbalance_settlement.py — Phase 5C imbalance settlement.

Applies dual pricing to the uninstructed delivery deviation recorded in Phase 4A:
  LONG  (over-delivery) -> settled at the long_price  (DA × 0.85 discount)
  SHORT (under-delivery) -> settled at the short_price (DA × 1.20 premium)
Reserve activation energy is NOT included here; it was settled in Phase 5B.

    python phase_5c_imbalance_settlement/run_imbalance_settlement.py --date 2026-06-26
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config
from common_layer.utilities import get_logger, AuditLogger
from common_layer.utilities import date_utils as du
from phase_5c_imbalance_settlement.imbalance_price_and_volume.ren_imbalance_price_loader import (
    fetch_imbalance_prices,
)
from phase_5c_imbalance_settlement.imbalance_price_and_volume.imbalance_volume_calculator import (
    compute_imbalance,
)
from phase_5c_imbalance_settlement.imbalance_settlement_calculation.imbalance_settlement_calculator import (
    settle_imbalance,
)

log = get_logger("phase5.imbalance")


def run_imbalance_settlement(delivery_date: str, config_dir=None) -> dict:
    cfg = load_config(config_dir)
    audit = AuditLogger()
    day = du.parse_date(delivery_date)
    hours = du.delivery_hours(day)
    isp_h = du.isp_duration_min(day) / 60.0

    rows = compute_imbalance(delivery_date, isp_h)
    if not rows:
        log.warning("[imbalance] no delivery data; run Phase 4A real-time first.")
        return {"status": "NO_DELIVERY"}

    short, long_ = fetch_imbalance_prices(delivery_date, hours, cfg)
    s = settle_imbalance(rows, short, long_)

    print("\n" + "=" * 56)
    print(f"  IMBALANCE SETTLEMENT  —  {delivery_date}  (dual pricing)")
    print("=" * 56)
    print(f"  Total imbalance volume : {s.total_imbalance_mwh:>12.2f} MWh")
    print(f"  Long revenue (sold)    : {s.long_revenue_eur:>12,.2f} EUR")
    print(f"  Short cost   (bought)  : {s.short_cost_eur:>12,.2f} EUR")
    print("  " + "-" * 40)
    print(f"  Net imbalance          : {s.net_eur:>12,.2f} EUR")
    print("=" * 56)

    audit.log("IMBALANCE_SETTLED", net_eur=s.net_eur,
              total_mwh=s.total_imbalance_mwh)
    return {"status": "OK", "net_imbalance_eur": s.net_eur,
            "long_revenue_eur": s.long_revenue_eur, "short_cost_eur": s.short_cost_eur,
            "total_imbalance_mwh": s.total_imbalance_mwh}


def main():
    p = argparse.ArgumentParser(description="Run Phase 5C imbalance settlement")
    p.add_argument("--date", required=True)
    p.add_argument("--config", default=None)
    args = p.parse_args()
    r = run_imbalance_settlement(args.date, args.config)
    print("\n  RESULT:", r.get("status"))
    sys.exit(0)


if __name__ == "__main__":
    main()
