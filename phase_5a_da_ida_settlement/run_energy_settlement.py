"""
run_energy_settlement.py — Phase 5A energy (DA + intraday) settlement.

Settles the DA position at the cleared DA price and each intraday gate's DELTA
at that gate's cleared price (DA -> IDA1 -> IDA2 -> IDA3 -> XBID), avoiding
double-counting. Writes an audit record and returns a revenue breakdown.

    python phase_5a_da_ida_settlement/run_energy_settlement.py --date 2026-06-26
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config
from common_layer.utilities import get_logger, AuditLogger
from common_layer.utilities import date_utils as du
from phase_5a_da_ida_settlement.energy_settlement_calculation.omie_settlement_price_loader import (
    fetch_settlement_prices,
)
from phase_5a_da_ida_settlement.energy_settlement_calculation.da_settlement_calculator import settle_da
from phase_5a_da_ida_settlement.energy_settlement_calculation.ida_settlement_calculator import (
    settle_intraday,
)

log = get_logger("phase5.energy")


def run_energy_settlement(delivery_date: str, config_dir=None) -> dict:
    load_config(config_dir)
    audit = AuditLogger()
    day = du.parse_date(delivery_date)
    hours = du.delivery_hours(day)

    da = settle_da(delivery_date, fetch_settlement_prices(delivery_date, "DA", hours))
    ida = settle_intraday(delivery_date,
                          lambda g, hs: fetch_settlement_prices(delivery_date, g, hs))
    total = da.revenue_eur + ida.total_revenue_eur

    print("\n" + "=" * 56)
    print(f"  ENERGY SETTLEMENT  —  {delivery_date}")
    print("=" * 56)
    print(f"  DA revenue        : {da.revenue_eur:>14,.2f} EUR")
    for gate, rev in ida.revenue_by_gate.items():
        print(f"  {gate} delta revenue : {rev:>14,.2f} EUR")
    print("  " + "-" * 40)
    print(f"  Total energy      : {total:>14,.2f} EUR")
    print("=" * 56)

    audit.log("ENERGY_SETTLED", da_revenue_eur=da.revenue_eur,
              ida_revenue_eur=ida.total_revenue_eur, total_eur=total)
    return {"status": "OK", "da_revenue_eur": da.revenue_eur,
            "ida_revenue_by_gate": ida.revenue_by_gate,
            "ida_revenue_eur": ida.total_revenue_eur, "total_energy_eur": total}


def main():
    p = argparse.ArgumentParser(description="Run Phase 5A energy settlement")
    p.add_argument("--date", required=True)
    p.add_argument("--config", default=None)
    args = p.parse_args()
    r = run_energy_settlement(args.date, args.config)
    print("\n  RESULT:", r.get("status"))
    sys.exit(0)


if __name__ == "__main__":
    main()
