"""
ren_reserve_settlement_loader.py — reserve settlement inputs from REN data.

Pulls the two settlement components for a reserve product:
  * committed capacity offer (up/dn MW + cap price) from ReserveStore,
  * activated energy per ISP (up/dn MW + energy price) from ActivationStore.
Live mode would reconcile against REN's published settlement; offline it reads
what Phases 3 and 4 stored.
"""
from __future__ import annotations

from typing import Dict, List

from common_layer.database import ReserveStore, ActivationStore


def load_capacity_offer(delivery_date: str, product: str) -> Dict[int, dict]:
    return ReserveStore().load_reserve(delivery_date, product)


def load_activations(delivery_date: str, product: str) -> List[dict]:
    return ActivationStore().load(delivery_date, product)
