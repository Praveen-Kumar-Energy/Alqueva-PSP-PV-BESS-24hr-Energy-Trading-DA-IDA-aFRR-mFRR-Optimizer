"""
run_ida3.py — Phase 2C IDA3 gate (intraday auction 3, closes D 10:00 CET).

Re-optimises hours H12-H24 against the IDA2 committed position.
H1-H11 are frozen (committed in IDA1/IDA2, not re-tradable).
Run DA, IDA1, IDA2 first.

    python phase_2c_ida3_intraday_auction_3/run_ida3.py --date 2026-06-22
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config
from phase_2c_ida3_intraday_auction_3.ida3_milp_reoptimiser.ida3_reoptimiser import optimise_ida3
from phase_2c_ida3_intraday_auction_3.ida3_bid_formatting.ida3_bid_formatter import (
    format_ida3_bids,
    to_sidc_payload,
    render_table,
)


def main():
    p = argparse.ArgumentParser(description="Run the Phase 2C IDA3 gate (H13-H24)")
    p.add_argument("--date", required=True, help="delivery date YYYY-MM-DD")
    p.add_argument("--config", default=None)
    p.add_argument("--no-pause", action="store_true")
    args = p.parse_args()

    result = optimise_ida3(args.date, load_config(args.config), no_pause=args.no_pause)
    status = result.get("status")
    print(f"\n  IDA3 gate result: {status}")

    if status == "SUBMITTED":
        bids = format_ida3_bids(
            committed=result["committed_net_mw"],
            new_net=result["new_net_mw"],
            ida3_prices=result["ida_prices"],
            tradable_hours=result["tradable_hours"],
        )
        print()
        print(render_table(bids))
        payload = to_sidc_payload(bids, args.date)
        print(f"\n  SIDC IDA3 payload: {len(payload['orders'])} orders  "
              f"net impact {payload['net_revenue_impact_eur']:+.2f} EUR")
    else:
        for k, v in result.items():
            if k != "status":
                print(f"    {k}: {v}")

    sys.exit(0 if status in ("SUBMITTED", "NO_CHANGE") else 1)


if __name__ == "__main__":
    main()
