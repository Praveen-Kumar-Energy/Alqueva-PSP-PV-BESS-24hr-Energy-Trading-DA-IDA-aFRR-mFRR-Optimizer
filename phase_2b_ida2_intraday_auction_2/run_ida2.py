"""
run_ida2.py — Phase 2B IDA2 gate (intraday auction 2, closes D-1 22:00 CET).

Re-optimises hours h3-h24 against the IDA1 committed position under IDA2 prices.
h1-h2 are frozen (already committed in IDA1, not re-tradable).
Run DA and IDA1 first.

    python phase_2b_ida2_intraday_auction_2/run_ida2.py --date 2026-06-22
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config
from phase_2b_ida2_intraday_auction_2.ida2_milp_reoptimiser.ida2_reoptimiser import optimise_ida2
from phase_2b_ida2_intraday_auction_2.ida2_bid_formatting.ida2_bid_formatter import (
    format_ida2_bids,
    to_sidc_payload,
    render_table,
)


def main():
    p = argparse.ArgumentParser(description="Run the Phase 2B IDA2 gate")
    p.add_argument("--date", required=True, help="delivery date YYYY-MM-DD")
    p.add_argument("--config", default=None)
    p.add_argument("--no-pause", action="store_true")
    args = p.parse_args()

    result = optimise_ida2(args.date, load_config(args.config), no_pause=args.no_pause)
    status = result.get("status")
    print(f"\n  IDA2 gate result: {status}")

    if status == "SUBMITTED":
        bids = format_ida2_bids(
            committed=result["committed_net_mw"],
            new_net=result["new_net_mw"],
            ida2_prices=result["ida_prices"],
            tradable_hours=result["tradable_hours"],
        )
        print()
        print(render_table(bids))
        payload = to_sidc_payload(bids, args.date)
        print(f"\n  SIDC IDA2 payload: {len(payload['orders'])} orders  "
              f"net impact {payload['net_revenue_impact_eur']:+.2f} EUR")
    else:
        for k, v in result.items():
            if k != "status":
                print(f"    {k}: {v}")

    sys.exit(0 if status in ("SUBMITTED", "NO_CHANGE") else 1)


if __name__ == "__main__":
    main()
