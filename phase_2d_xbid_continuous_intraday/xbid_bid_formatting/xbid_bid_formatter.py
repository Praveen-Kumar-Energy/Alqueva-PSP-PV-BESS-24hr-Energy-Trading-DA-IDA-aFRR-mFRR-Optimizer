"""
xbid_bid_formatter.py — format XBID continuous intraday orders.

XBID (Single Intraday Coupling continuous market) differs from IDA auctions:
  * Orders placed continuously at any time (not at a single gate close)
  * Each order capped at xbid_max_volume_per_order_mw per delivery hour
  * Closes 1 hour before each delivery period
  * Baseline: latest committed position across all prior gates (DA + IDA1/2/3)

Order sides:
  BUY  — increase net position (more generation / reduce pumping)
  SELL — decrease net position (reduce generation / increase pumping)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class XBIDOrder:
    hour: int
    committed_mwh: float        # net position from latest gate (IDA3 if run, else IDA2/IDA1/DA)
    new_mwh: float              # new target net position after XBID order
    order_mwh: float            # signed delta = new - committed
    xbid_price_eur_mwh: float   # XBID proxy price for that hour
    revenue_impact_eur: float   # order_mwh * xbid_price (expected P&L change)
    side: str                   # "BUY" | "SELL"
    window: str                 # "W1" | "W2"


def format_xbid_orders(committed: Dict[int, float],
                       new_net: Dict[int, float],
                       xbid_prices: Dict[int, float],
                       open_hours: List[int],
                       window: str,
                       min_delta_mwh: float = 0.1,
                       dt: float = 1.0) -> List[XBIDOrder]:
    """Build XBID orders for all open hours that have a material position change.

    Args:
        committed     : {hour: net_mw} latest committed position (all prior gates)
        new_net       : {hour: net_mw} re-optimised schedule
        xbid_prices   : {hour: EUR/MWh} XBID proxy price (IDA3 + drift)
        open_hours    : hours still tradable at this check window
        window        : "W1" or "W2"
        min_delta_mwh : skip orders below this threshold (avoid noise)
        dt            : hour duration [h]
    Returns:
        List[XBIDOrder] sorted by hour, only hours with |order_mwh| >= min_delta_mwh
    """
    orders: List[XBIDOrder] = []
    for h in sorted(open_hours):
        comm_mwh = float(committed.get(h, 0.0)) * dt
        new_mwh  = float(new_net.get(h, 0.0)) * dt
        delta    = new_mwh - comm_mwh
        if abs(delta) < min_delta_mwh:
            continue
        price  = float(xbid_prices.get(h, 0.0))
        impact = delta * price
        side   = "BUY" if delta > 0 else "SELL"
        orders.append(XBIDOrder(
            hour=h,
            committed_mwh=round(comm_mwh, 3),
            new_mwh=round(new_mwh, 3),
            order_mwh=round(delta, 3),
            xbid_price_eur_mwh=round(price, 2),
            revenue_impact_eur=round(impact, 2),
            side=side,
            window=window,
        ))
    return orders


def to_xbid_payload(orders: List[XBIDOrder], delivery_date: str,
                    window: str, unit_id: str = "ALQUEVA") -> dict:
    """Structured payload for the (stubbed) XBID order submitter.

    Real XBID submission uses SIDC API (ENTSO-E XBID connectivity node).
    Only hours with material position changes are included.
    """
    return {
        "market"                : "SIDC_XBID",
        "operator"              : "OMIE",
        "unit"                  : unit_id,
        "delivery_date"         : delivery_date,
        "gate"                  : "XBID",
        "check_window"          : window,
        "window_description"    : "D-1 18:30 CET" if window == "W1" else "D 09:30 CET",
        "market_type"           : "continuous",
        "resolution"            : "hourly",
        "total_order_mwh"       : round(sum(abs(o.order_mwh) for o in orders), 3),
        "net_revenue_impact_eur": round(sum(o.revenue_impact_eur for o in orders), 2),
        "orders": [
            {
                "hour"              : o.hour,
                "committed_mwh"     : o.committed_mwh,
                "new_mwh"           : o.new_mwh,
                "order_mwh"         : o.order_mwh,
                "price_eur_mwh"     : o.xbid_price_eur_mwh,
                "side"              : o.side,
                "revenue_impact_eur": o.revenue_impact_eur,
            }
            for o in orders
        ],
    }


def render_table(orders: List[XBIDOrder], window: str) -> str:
    """Terminal table for operator review before XBID order submission."""
    window_desc = "D-1 18:30 CET (W1 — full day open)" if window == "W1" \
                  else "D 09:30 CET (W2 — hours >= H11 open)"
    lines = [
        f"  XBID continuous market — check window {window} ({window_desc})",
        f"  {'Hour':<5} {'Committed':>11} {'New':>9} {'Order MWh':>11} "
        f"{'Price':>8} {'Impact EUR':>12} {'Side':<6}",
        "  " + "-" * 63,
    ]
    for o in orders:
        lines.append(
            f"  H{o.hour:02d}  {o.committed_mwh:>+11.2f} {o.new_mwh:>+9.2f} "
            f"{o.order_mwh:>+11.3f} {o.xbid_price_eur_mwh:>8.2f} "
            f"{o.revenue_impact_eur:>+12.2f}  {o.side:<6}"
        )
    lines.append("  " + "-" * 63)
    total = sum(o.revenue_impact_eur for o in orders)
    lines.append(f"  {'Orders: ' + str(len(orders)):<30} {'TOTAL IMPACT:':>25} "
                 f"{total:>+12.2f} EUR")
    return "\n".join(lines)
