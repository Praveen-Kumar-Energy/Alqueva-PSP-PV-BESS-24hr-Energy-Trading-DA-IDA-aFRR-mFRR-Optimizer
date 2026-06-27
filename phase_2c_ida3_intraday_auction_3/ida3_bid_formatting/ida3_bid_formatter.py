"""
ida3_bid_formatter.py — format IDA3 re-optimised position as SIDC delta bids.

IDA3 gate: closes D 10:00 CET, tradable hours H12-H24 (H1-H11 frozen after IDA1/IDA2).
Baseline: IDA2 committed position (not DA or IDA1).

Delta bids = new_net - committed (IDA2 baseline):
    positive delta (BUY_BACK)  — increase generation / reduce pump
    negative delta (SELL_DOWN) — reduce generation / increase pump

SIDC submission covers only tradable hours (H12-H24); frozen hours (H1-H11)
are not included in the order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class IDA3DeltaBid:
    hour: int
    committed_mwh: float       # IDA2 committed volume (net * dt)
    reoptimised_mwh: float     # New IDA3 net position * dt
    delta_mwh: float           # = reoptimised - committed (signed)
    ida3_price_eur_mwh: float  # IDA3 price for that hour
    revenue_impact_eur: float  # delta_mwh * ida3_price (expected P&L change)
    side: str                  # "BUY_BACK" | "SELL_DOWN" | "NO_CHANGE"


def format_ida3_bids(committed: Dict[int, float],
                     new_net: Dict[int, float],
                     ida3_prices: Dict[int, float],
                     tradable_hours: List[int],
                     dt: float = 1.0) -> List[IDA3DeltaBid]:
    """Build IDA3 delta bids for all tradable hours (H12-H24).

    Args:
        committed     : {hour: net_mw} from the IDA2 committed position
        new_net       : {hour: net_mw} from the IDA3 re-optimised schedule
        ida3_prices   : {hour: EUR/MWh} IDA3 forecast price
        tradable_hours: hours within the IDA3 gate window (H12-H24)
        dt            : hour duration [h], 1.0 for standard hourly
    Returns:
        List[IDA3DeltaBid] sorted by hour
    """
    bids: List[IDA3DeltaBid] = []
    for h in sorted(tradable_hours):
        comm_mwh  = float(committed.get(h, 0.0)) * dt
        reopt_mwh = float(new_net.get(h, 0.0)) * dt
        delta     = reopt_mwh - comm_mwh
        price     = float(ida3_prices.get(h, 0.0))
        impact    = delta * price

        if delta > 1e-4:
            side = "BUY_BACK"
        elif delta < -1e-4:
            side = "SELL_DOWN"
        else:
            side = "NO_CHANGE"

        bids.append(IDA3DeltaBid(
            hour=h,
            committed_mwh=round(comm_mwh, 3),
            reoptimised_mwh=round(reopt_mwh, 3),
            delta_mwh=round(delta, 3),
            ida3_price_eur_mwh=round(price, 2),
            revenue_impact_eur=round(impact, 2),
            side=side,
        ))
    return bids


def to_sidc_payload(bids: List[IDA3DeltaBid], delivery_date: str,
                    unit_id: str = "ALQUEVA") -> dict:
    """Build the structured payload for the (stubbed) SIDC IDA3 submitter.

    Real SIDC submission uses CIM XML (IEC 62325) via ENTSO-E connectivity node.
    Frozen hours H1-H11 are NOT included — IDA3 only covers H12-H24.
    """
    active = [b for b in bids if b.side != "NO_CHANGE"]
    return {
        "market"                : "SIDC_IDA3",
        "operator"              : "OMIE",
        "unit"                  : unit_id,
        "delivery_date"         : delivery_date,
        "gate"                  : "IDA3",
        "gate_close_cet"        : f"{delivery_date} 10:00",
        "tradable_hours"        : "H12-H24",
        "frozen_hours"          : "H1-H11",
        "resolution"            : "hourly",
        "total_delta_mwh"       : round(sum(abs(b.delta_mwh) for b in active), 3),
        "net_revenue_impact_eur": round(sum(b.revenue_impact_eur for b in active), 2),
        "orders": [
            {
                "hour"              : b.hour,
                "committed_mwh"     : b.committed_mwh,
                "reoptimised_mwh"   : b.reoptimised_mwh,
                "delta_mwh"         : b.delta_mwh,
                "price_eur_mwh"     : b.ida3_price_eur_mwh,
                "side"              : b.side,
                "revenue_impact_eur": b.revenue_impact_eur,
            }
            for b in active
        ],
    }


def render_table(bids: List[IDA3DeltaBid]) -> str:
    """Terminal table for operator review before IDA3 submission."""
    lines = [
        "  Note: H1-H11 FROZEN (committed in IDA1/IDA2, not re-tradable)",
        f"  {'Hour':<5} {'Committed':>11} {'IDA3':>9} {'Delta MWh':>11} "
        f"{'Price':>8} {'Impact EUR':>12} {'Action':<12}",
        "  " + "-" * 65,
    ]
    for b in bids:
        action = f"  <-- {b.side}" if b.side != "NO_CHANGE" else ""
        lines.append(
            f"  H{b.hour:02d}  {b.committed_mwh:>+11.2f} {b.reoptimised_mwh:>+9.2f} "
            f"{b.delta_mwh:>+11.3f} {b.ida3_price_eur_mwh:>8.2f} "
            f"{b.revenue_impact_eur:>+12.2f}{action}"
        )
    lines.append("  " + "-" * 65)
    active = [b for b in bids if b.side != "NO_CHANGE"]
    total  = sum(b.revenue_impact_eur for b in active)
    lines.append(f"  {'Trades: ' + str(len(active)):<30} {'TOTAL IMPACT:':>25} "
                 f"{total:>+12.2f} EUR")
    return "\n".join(lines)
