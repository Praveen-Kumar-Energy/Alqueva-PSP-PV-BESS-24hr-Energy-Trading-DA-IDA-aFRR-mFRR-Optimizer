"""
da_settlement_calculator.py — Day-Ahead settlement.

DA revenue = committed DA volume (MWh, + sell / - buy) x DA cleared price, summed
over the day. A sell earns; a buy (pumping) costs. Reads the committed DA position
from PositionStore.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from common_layer.database import PositionStore


@dataclass
class DASettlement:
    revenue_eur: float
    per_hour: Dict[int, float]


def settle_da(delivery_date: str, settle_prices: Dict[int, float]) -> DASettlement:
    da = PositionStore().load_position(delivery_date, "DA")
    per_hour: Dict[int, float] = {}
    total = 0.0
    for h, rec in da.items():
        rev = rec["volume_mwh"] * settle_prices.get(h, rec["price_eur_mwh"])
        per_hour[h] = rev
        total += rev
    return DASettlement(revenue_eur=total, per_hour=per_hour)
