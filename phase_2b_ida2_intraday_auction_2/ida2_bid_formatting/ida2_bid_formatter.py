"""
ida2_bid_formatter.py — format IDA2 re-optimised position as SIDC delta bids.

IDA2 gate: closes D-1 22:00 CET, tradable hours h3-h24 (h1-h2 frozen after IDA1).
Baseline: IDA1 committed position (not DA).

Delta bids = new_net - committed (IDA1 baseline):
    positive delta (BUY_BACK)  — increase generation / reduce pump
    negative delta (SELL_DOWN) — reduce generation / increase pump

SIDC submission covers only the tradable hours (h3-h24); frozen hours (h1-h2)
are not included in the order because they cannot be changed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class IDA2DeltaBid:
    hour: int
    committed_mwh: float       # IDA1 committed volume (net * dt)
    reoptimised_mwh: float     # New IDA2 net position * dt
    delta_mwh: float           # = reoptimised - committed (signed)
    ida2_price_eur_mwh: float  # IDA2 price for that hour
    revenue_impact_eur: float  # delta_mwh * ida2_price (expected P&L change)
    side: str                  # "BUY_BACK" | "SELL_DOWN" | "NO_CHANGE"


def format_ida2_bids(committed: Dict[int, float],
                     new_net: Dict[int, float],
                     ida2_prices: Dict[int, float],
                     tradable_hours: List[int],
                     dt: float = 1.0) -> List[IDA2DeltaBid]:
    """Build IDA2 delta bids for all tradable hours (h3-h24).

    Args:
        committed     : {hour: net_mw} from the IDA1 committed position
        new_net       : {hour: net_mw} from the IDA2 re-optimised schedule
        ida2_prices   : {hour: EUR/MWh} IDA2 forecast price
        tradable_hours: hours within the IDA2 gate window (h3-h24)
        dt            : hour duration [h], 1.0 for standard hourly
    Returns:
        List[IDA2DeltaBid] sorted by hour
    """
    bids: List[IDA2DeltaBid] = []
    for h in sorted(tradable_hours):
        comm_mwh  = float(committed.get(h, 0.0)) * dt
        reopt_mwh = float(new_net.get(h, 0.0)) * dt
        delta     = reopt_mwh - comm_mwh
        price     = float(ida2_prices.get(h, 0.0))
        impact    = delta * price

        if delta > 1e-4:
            side = "BUY_BACK"
        elif delta < -1e-4:
            side = "SELL_DOWN"
        else:
            side = "NO_CHANGE"

        bids.append(IDA2DeltaBid(
            hour=h,
            committed_mwh=round(comm_mwh, 3),
            reoptimised_mwh=round(reopt_mwh, 3),
            delta_mwh=round(delta, 3),
            ida2_price_eur_mwh=round(price, 2),
            revenue_impact_eur=round(impact, 2),
            side=side,
        ))
    return bids


def to_sidc_payload(bids: List[IDA2DeltaBid], delivery_date: str,
                    unit_id: str = "ALQUEVA") -> dict:
    """Build the structured payload for the (stubbed) SIDC IDA2 submitter.

    Real SIDC submission uses CIM XML (IEC 62325) via ENTSO-E connectivity node.
    Frozen hours h1-h2 are NOT included — IDA2 only covers h3-h24.
    """
    active = [b for b in bids if b.side != "NO_CHANGE"]
    return {
        "market"                : "SIDC_IDA2",
        "operator"              : "OMIE",
        "unit"                  : unit_id,
        "delivery_date"         : delivery_date,
        "gate"                  : "IDA2",
        "gate_close_cet"        : f"{delivery_date} 22:00",
        "tradable_hours"        : "h3-h24",
        "frozen_hours"          : "h1-h2",
        "resolution"            : "hourly",
        "total_delta_mwh"       : round(sum(abs(b.delta_mwh) for b in active), 3),
        "net_revenue_impact_eur": round(sum(b.revenue_impact_eur for b in active), 2),
        "orders": [
            {
                "hour"              : b.hour,
                "committed_mwh"     : b.committed_mwh,
                "reoptimised_mwh"   : b.reoptimised_mwh,
                "delta_mwh"         : b.delta_mwh,
                "price_eur_mwh"     : b.ida2_price_eur_mwh,
                "side"              : b.side,
                "revenue_impact_eur": b.revenue_impact_eur,
            }
            for b in active
        ],
    }


def render_table(bids: List[IDA2DeltaBid]) -> str:
    """Terminal table for operator review before IDA2 submission."""
    lines = [
        "  Note: h1-h2 FROZEN (committed in IDA1, not re-tradable)",
        f"  {'Hour':<5} {'Committed':>11} {'IDA2':>9} {'Delta MWh':>11} "
        f"{'Price':>8} {'Impact EUR':>12} {'Action':<12}",
        "  " + "-" * 65,
    ]
    for b in bids:
        action = f"  <-- {b.side}" if b.side != "NO_CHANGE" else ""
        lines.append(
            f"  H{b.hour:02d}  {b.committed_mwh:>+11.2f} {b.reoptimised_mwh:>+9.2f} "
            f"{b.delta_mwh:>+11.3f} {b.ida2_price_eur_mwh:>8.2f} "
            f"{b.revenue_impact_eur:>+12.2f}{action}"
        )
    lines.append("  " + "-" * 65)
    active = [b for b in bids if b.side != "NO_CHANGE"]
    total  = sum(b.revenue_impact_eur for b in active)
    lines.append(f"  {'Trades: ' + str(len(active)):<30} {'TOTAL IMPACT:':>25} "
                 f"{total:>+12.2f} EUR")
    return "\n".join(lines)
