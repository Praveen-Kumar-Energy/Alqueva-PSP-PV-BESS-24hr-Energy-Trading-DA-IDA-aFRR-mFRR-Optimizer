"""
trader_approval_prompt.py — four-eyes approval at the DA gate (spec FR-2.7, INV-9).

The optimiser never submits on its own. After the physical bid check and the
risk check pass, the recommended bid is shown to the trader, who approves [A] or
rejects [R]. This is the one human gate in the DA pipeline; the demo runs it live
in front of the interviewer.

For the IDA / reserve gates the convention is a single ENTER pause instead of
A/R, handled by their own runners.
"""
from __future__ import annotations

from typing import List

from common_layer.optimisation_model.core_milp_solver import GateResults
from phase_1_da_day_ahead_bidding.da_bid_formatting.da_bid_formatter import DABid, render_table


def request_da_approval(bids: List[DABid], results: GateResults,
                        price_source: str, solve_time: float,
                        gate_close_cet: str) -> bool:
    """Show the recommendation and ask the trader. Returns True if approved."""
    print("\n" + "=" * 62)
    print("  DA BID RECOMMENDATION  —  delivery via OMIE (MIBEL)")
    print("=" * 62)
    print(f"  Price source     : {price_source}")
    print(f"  Solve time       : {solve_time:.2f} s")
    print(f"  Gate closes (CET): {gate_close_cet}   <-- submit before this")
    print(f"  Expected energy revenue : {results.energy_revenue_eur:>12,.2f} EUR")
    print(f"  Objective (net of costs): {results.objective_eur:>12,.2f} EUR")
    print()
    print(render_table(bids))
    print("=" * 62)
    try:
        resp = input("\n  Type  A  to approve,  R  to reject : ").strip().upper()
    except (EOFError, KeyboardInterrupt):
        return False
    # Accept "A", "APPROVE", "YES", "Y", or anything starting with A.
    return resp.startswith("A") or resp in ("YES", "Y")
