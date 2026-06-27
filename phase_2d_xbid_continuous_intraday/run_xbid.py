"""
run_xbid.py — Phase 2D XBID continuous intraday gate.

Evaluates the still-open hours at a check window and places capped opportunistic
orders when the price beats the spread. Two demo windows:
    W1  D-1 18:30  (all hours open)
    W2  D 09:30    (hours 11-24 open; earlier hours closed)
Run DA (and IDAs) first so a committed baseline exists.

    python phase_2d_xbid_continuous_intraday/run_xbid.py --date 2026-06-26 --window W1
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config
from phase_2d_xbid_continuous_intraday.xbid_milp_optimiser.xbid_optimiser import optimise_xbid
from phase_2d_xbid_continuous_intraday.xbid_bid_formatting.xbid_bid_formatter import (
    format_xbid_orders,
    to_xbid_payload,
    render_table,
)


def main():
    p = argparse.ArgumentParser(description="Run the Phase 2D XBID gate")
    p.add_argument("--date", required=True, help="delivery date YYYY-MM-DD")
    p.add_argument("--window", default="W1", choices=["W1", "W2"],
                   help="check window: W1 (D-1 18:30) or W2 (D 09:30)")
    p.add_argument("--config", default=None)
    p.add_argument("--no-pause", action="store_true")
    args = p.parse_args()

    result = optimise_xbid(args.date, load_config(args.config),
                           window=args.window, no_pause=args.no_pause)
    status = result.get("status")
    print(f"\n  XBID {args.window} gate result: {status}")

    if status == "SUBMITTED":
        orders = format_xbid_orders(
            committed=result["committed_net_mw"],
            new_net=result["new_net_mw"],
            xbid_prices=result["xbid_prices"],
            open_hours=result["open_hours"],
            window=args.window,
        )
        print()
        print(render_table(orders, args.window))
        payload = to_xbid_payload(orders, args.date, args.window)
        print(f"\n  XBID payload: {len(payload['orders'])} orders  "
              f"net impact {payload['net_revenue_impact_eur']:+.2f} EUR")
    else:
        for k, v in result.items():
            if k != "status":
                print(f"    {k}: {v}")

    sys.exit(0 if status in ("SUBMITTED", "NO_CHANGE") else 1)


if __name__ == "__main__":
    main()
