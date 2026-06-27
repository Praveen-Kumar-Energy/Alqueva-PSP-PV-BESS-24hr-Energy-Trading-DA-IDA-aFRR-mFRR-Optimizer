"""
ida_settlement_calculator.py — intraday (IDA1/2/3 + XBID) settlement.

Each intraday gate is settled on the DELTA it traded versus the prior committed
position, valued at that gate's cleared price:
    delta_gate[h] = pos_gate[h] - pos_prior[h]
    revenue      += delta_gate[h] * settle_gate[h]
Walking the gates in order (DA -> IDA1 -> IDA2 -> IDA3 -> XBID) avoids double
counting: each gate only earns on what it changed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List

from common_layer.database import PositionStore

_GATE_ORDER = ["DA", "IDA1", "IDA2", "IDA3", "XBID"]


@dataclass
class IDASettlement:
    revenue_by_gate: Dict[str, float] = field(default_factory=dict)
    total_revenue_eur: float = 0.0


def settle_intraday(delivery_date: str,
                    settle_price_fn: Callable[[str, List[int]], Dict[int, float]]
                    ) -> IDASettlement:
    """settle_price_fn(gate, hours) -> {hour: cleared price}."""
    store = PositionStore()
    result = IDASettlement()

    prior: Dict[int, float] = {}                 # running committed net per hour
    da = store.load_position(delivery_date, "DA")
    for h, rec in da.items():
        prior[h] = rec["volume_mwh"]

    for gate in _GATE_ORDER[1:]:                 # intraday gates only
        pos = store.load_position(delivery_date, gate)
        if not pos:
            continue
        hours = sorted(pos)
        prices = settle_price_fn(gate, hours)
        gate_rev = 0.0
        for h in hours:
            delta = pos[h]["volume_mwh"] - prior.get(h, 0.0)
            gate_rev += delta * prices.get(h, pos[h]["price_eur_mwh"])
            prior[h] = pos[h]["volume_mwh"]      # advance running position
        result.revenue_by_gate[gate] = gate_rev
        result.total_revenue_eur += gate_rev

    return result
