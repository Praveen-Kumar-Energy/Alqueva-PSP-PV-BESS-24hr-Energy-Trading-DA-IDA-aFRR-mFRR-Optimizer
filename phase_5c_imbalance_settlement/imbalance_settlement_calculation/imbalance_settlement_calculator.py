"""
imbalance_settlement_calculator.py — value imbalance under dual pricing.

Per ISP:
    LONG  (imbalance > 0): sold at the long price  (discount)  -> revenue
    SHORT (imbalance < 0): bought back at the short price (premium) -> cost
Net imbalance settlement is revenue - cost; under dual pricing it is typically a
net cost, which is the incentive to deliver exactly the committed schedule.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from phase_5c_imbalance_settlement.imbalance_price_and_volume.imbalance_volume_calculator import (
    ImbalanceRow,
)


@dataclass
class ImbalanceSettlement:
    long_revenue_eur: float
    short_cost_eur: float
    total_imbalance_mwh: float

    @property
    def net_eur(self) -> float:
        return self.long_revenue_eur - self.short_cost_eur


def settle_imbalance(rows: List[ImbalanceRow], short_price: Dict[int, float],
                     long_price: Dict[int, float]) -> ImbalanceSettlement:
    long_rev = short_cost = total_abs = 0.0
    for r in rows:
        total_abs += abs(r.imbalance_mwh)
        if r.imbalance_mwh > 0:                       # long -> sell at discount
            long_rev += r.imbalance_mwh * long_price.get(r.hour, 0.0)
        elif r.imbalance_mwh < 0:                     # short -> buy back at premium
            short_cost += (-r.imbalance_mwh) * short_price.get(r.hour, 0.0)
    return ImbalanceSettlement(long_revenue_eur=long_rev, short_cost_eur=short_cost,
                               total_imbalance_mwh=total_abs)
