"""
afrr_settlement_calculator.py — reserve settlement (generic; used by aFRR & mFRR).

Two revenue streams:
  * capacity (availability): for every offered hour, (up_mw*cap_up + dn_mw*cap_dn)
    x 1 hour — paid whether or not activated.
  * activation (energy): for every activated ISP,
        up_mw  x isp_hours x up_price_eur_mwh
      + dn_mw  x isp_hours x dn_price_eur_mwh
    Up and down activations carry DIFFERENT prices (scarcity premium vs. discount
    on the hour's DA price), so they must be settled separately with their own
    price fields rather than a single blended price.

The function is product-agnostic; mFRR settlement reuses it.
"""
from __future__ import annotations

from dataclasses import dataclass

from phase_5b_reserve_settlement.reserve_settlement_calculation.ren_reserve_settlement_loader import (
    load_capacity_offer, load_activations,
)


@dataclass
class ReserveSettlement:
    product: str
    capacity_eur: float
    activation_eur: float

    @property
    def total_eur(self) -> float:
        return self.capacity_eur + self.activation_eur


def settle_reserve(delivery_date: str, product: str, isp_hours: float) -> ReserveSettlement:
    offers = load_capacity_offer(delivery_date, product)
    capacity = sum(o["up_mw"] * o["cap_up_eur_mw"] + o["dn_mw"] * o["cap_dn_eur_mw"]
                   for o in offers.values())     # x 1 hour per offered hour

    activations = load_activations(delivery_date, product)
    # Separate up/dn prices because the activation engine stores them independently.
    # Use eff_isp_h when stored: effective_isp_h = (isp_min - fat_min/2)/60 accounts
    # for energy lost during the FAT ramp; fall back to isp_hours when not present.
    activation = sum(
        a["up_mw"] * a.get("eff_isp_h", isp_hours) * a["up_price_eur_mwh"]
        + a["dn_mw"] * a.get("eff_isp_h", isp_hours) * a["dn_price_eur_mwh"]
        for a in activations
    )

    return ReserveSettlement(product=product, capacity_eur=capacity, activation_eur=activation)


def settle_afrr(delivery_date: str, isp_hours: float) -> ReserveSettlement:
    return settle_reserve(delivery_date, "aFRR", isp_hours)
