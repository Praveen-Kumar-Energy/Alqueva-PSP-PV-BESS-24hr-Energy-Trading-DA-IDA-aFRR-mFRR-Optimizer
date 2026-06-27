"""
run_ida1.py — Phase 2A IDA1 gate (intraday auction 1, closes D-1 15:00 CET).

Re-optimises the full day against the DA committed position under IDA1 prices,
applies the no-churn threshold, and (if worthwhile) re-bids via SIDC.
Run DA first so a committed baseline exists.

Pipeline:
    1. load config
    2. shared IDA engine: load DA baseline, ML IDA1 prices, MILP re-solve,
       no-churn threshold (PR-14), physical check, risk check
    3. if SUBMITTED: format SIDC delta bids, print table, save payload

Run:
    python phase_2a_ida1_intraday_auction_1/run_ida1.py --date 2026-06-22
    python phase_2a_ida1_intraday_auction_1/run_ida1.py --date 2026-06-22 --no-pause
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config
from phase_2a_ida1_intraday_auction_1.ida1_milp_reoptimiser.ida1_reoptimiser import optimise_ida1
from phase_2a_ida1_intraday_auction_1.ida1_bid_formatting.ida1_bid_formatter import (
    format_ida1_bids, to_sidc_payload, render_table,
)


def main():
    p = argparse.ArgumentParser(description="Run the Phase 2A IDA1 gate")
    p.add_argument("--date", required=True, help="delivery date YYYY-MM-DD")
    p.add_argument("--config", default=None)
    p.add_argument("--no-pause", action="store_true", help="skip the operator ENTER pause")
    args = p.parse_args()

    result = optimise_ida1(args.date, load_config(args.config), no_pause=args.no_pause)
    status = result.get("status")

    print(f"\n  RESULT: {status}")

    if status == "SUBMITTED":
        # Format and display SIDC delta bids
        bids = format_ida1_bids(
            committed=result["committed_net_mw"],
            new_net=result["new_net_mw"],
            ida1_prices=result["ida_prices"],
            tradable_hours=result["tradable_hours"],
        )
        print("\n  IDA1 DELTA BIDS (SIDC submission):")
        print(render_table(bids))

        payload = to_sidc_payload(bids, args.date)
        print(f"\n  SIDC payload: {len(payload['orders'])} active orders")
        print(f"    Total one-way vol : {payload['total_delta_mwh']:.3f} MWh")
        print(f"    Net revenue impact: {payload['net_revenue_impact_eur']:+.2f} EUR")
        print(f"    Ref: {result.get('ref')}")

    elif status == "NO_CHANGE":
        print(f"    improvement_eur  : {result.get('improvement_eur', 0):+.2f}")
        print(f"    one_way_vol_mwh  : {result.get('one_way_vol_mwh', 0):.3f}")

    else:
        for k, v in result.items():
            if k != "status":
                print(f"    {k}: {v}")

    sys.exit(0 if status in ("SUBMITTED", "NO_CHANGE") else 1)


if __name__ == "__main__":
    main()
