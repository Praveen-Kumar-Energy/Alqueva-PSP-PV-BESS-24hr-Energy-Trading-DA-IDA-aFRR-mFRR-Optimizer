"""
ida1_bid_formatter.py — format IDA1 re-optimised position as SIDC delta bids.

IDA bids differ from DA bids: the unit is not submitting a fresh schedule from
scratch; it is correcting its already-committed DA position. Each IDA bid is a
*delta* — positive delta = buy back (increase generation / reduce pump),
negative delta = sell down (reduce generation / increase pump).

SIDC (Single Intraday Coupling) accepts delta orders for each delivery hour.
The physical schedule submitted is the full re-optimised net position; the per-
hour deltas are what get traded against the intraday order book.

This module:
    1. format_ida1_bids() — build a list of IDA1DeltaBid dataclasses
    2. to_sidc_payload()  — structure the stub SIDC XML dict
    3. render_table()     — human-readable terminal table for the operator
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class IDA1DeltaBid:
    hour: int
    committed_mwh: float      # DA committed volume (net position * dt)
    reoptimised_mwh: float    # New IDA1 net position * dt
    delta_mwh: float          # = reoptimised - committed  (signed)
    ida1_price_eur_mwh: float # IDA1 price for that hour
    revenue_impact_eur: float # delta_mwh * ida1_price (expected P&L change)
    side: str                 # "BUY_BACK" | "SELL_DOWN" | "NO_CHANGE"


def format_ida1_bids(committed: Dict[int, float],
                     new_net: Dict[int, float],
                     ida1_prices: Dict[int, float],
                     tradable_hours: List[int],
                     dt: float = 1.0) -> List[IDA1DeltaBid]:
    """Build IDA1 delta bids for all tradable hours.

    Args:
        committed     : {hour: net_mw} from the DA committed position
        new_net       : {hour: net_mw} from the IDA1 re-optimised schedule
        ida1_prices   : {hour: EUR/MWh} IDA1 forecast price
        tradable_hours: hours within the IDA1 gate window (all 24 for IDA1)
        dt            : hour duration [h], 1.0 for standard hourly
    Returns:
        List[IDA1DeltaBid] sorted by hour
    """
    bids: List[IDA1DeltaBid] = []
    for h in sorted(tradable_hours):
        comm_mwh  = float(committed.get(h, 0.0)) * dt
        reopt_mwh = float(new_net.get(h, 0.0)) * dt
        delta     = reopt_mwh - comm_mwh
        price     = float(ida1_prices.get(h, 0.0))
        impact    = delta * price

        if delta > 1e-4:
            side = "BUY_BACK"    # increasing generation / reducing pump position
        elif delta < -1e-4:
            side = "SELL_DOWN"   # reducing generation / increasing pump position
        else:
            side = "NO_CHANGE"

        bids.append(IDA1DeltaBid(
            hour=h,
            committed_mwh=round(comm_mwh, 3),
            reoptimised_mwh=round(reopt_mwh, 3),
            delta_mwh=round(delta, 3),
            ida1_price_eur_mwh=round(price, 2),
            revenue_impact_eur=round(impact, 2),
            side=side,
        ))
    return bids


def to_sidc_payload(bids: List[IDA1DeltaBid], delivery_date: str,
                    unit_id: str = "ALQUEVA") -> dict:
    """Build the structured payload that the (stubbed) SIDC submitter would send.

    Real SIDC submission uses CIM XML (IEC 62325) via ENTSO-E connectivity node.
    Stubbed here as a dict with the same logical content.
    """
    active = [b for b in bids if b.side != "NO_CHANGE"]
    return {
        "market"         : "SIDC_IDA1",
        "operator"       : "OMIE",
        "unit"           : unit_id,
        "delivery_date"  : delivery_date,
        "gate"           : "IDA1",
        "gate_close_cet" : f"{delivery_date} 15:00",
        "resolution"     : "hourly",
        "total_delta_mwh": round(sum(abs(b.delta_mwh) for b in active), 3),
        "net_revenue_impact_eur": round(sum(b.revenue_impact_eur for b in active), 2),
        "orders": [
            {
                "hour"              : b.hour,
                "committed_mwh"     : b.committed_mwh,
                "reoptimised_mwh"   : b.reoptimised_mwh,
                "delta_mwh"         : b.delta_mwh,
                "price_eur_mwh"     : b.ida1_price_eur_mwh,
                "side"              : b.side,
                "revenue_impact_eur": b.revenue_impact_eur,
            }
            for b in active
        ],
    }


def render_table(bids: List[IDA1DeltaBid]) -> str:
    """Terminal table for operator review before IDA1 submission."""
    lines = [
        f"  {'Hour':<5} {'Committed':>11} {'IDA1':>9} {'Delta MWh':>11} "
        f"{'Price':>8} {'Impact EUR':>12} {'Action':<12}",
        "  " + "-" * 65,
    ]
    for b in bids:
        if b.side == "NO_CHANGE":
            action = ""
        else:
            action = f"  <-- {b.side}"
        lines.append(
            f"  H{b.hour:02d}  {b.committed_mwh:>+11.2f} {b.reoptimised_mwh:>+9.2f} "
            f"{b.delta_mwh:>+11.3f} {b.ida1_price_eur_mwh:>8.2f} "
            f"{b.revenue_impact_eur:>+12.2f}{action}"
        )
    lines.append("  " + "-" * 65)
    active = [b for b in bids if b.side != "NO_CHANGE"]
    total_impact = sum(b.revenue_impact_eur for b in active)
    lines.append(f"  {'Trades: ' + str(len(active)):<30} {'TOTAL IMPACT:':>25} "
                 f"{total_impact:>+12.2f} EUR")
    return "\n".join(lines)
