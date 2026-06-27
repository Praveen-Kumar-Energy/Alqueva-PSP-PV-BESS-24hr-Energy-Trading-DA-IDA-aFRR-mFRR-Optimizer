"""
mfrr_offer_checker.py — Phase 3B mFRR offer validity checker.

Verifies the mFRR offer PLUS the prior aFRR commitment PLUS the energy position
fit the headroom envelope (PR-11 across products), and that the offer is
deliverable within the 12.5-min mFRR FAT (PR-12).
"""
from __future__ import annotations

from typing import Dict, List

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model.reserve_offer_builder import (
    check_reserve_offers, ReserveOffer,
)


def check_mfrr_offers(offers: Dict[int, ReserveOffer], committed_net: Dict[int, float],
                      reserved_up: Dict[int, float], reserved_dn: Dict[int, float],
                      cfg: AppConfig) -> List[str]:
    return check_reserve_offers(
        offers=offers,
        committed_net=committed_net,
        cfg=cfg,
        fat_min=cfg.market.mfrr.fat_min,
        product="mFRR",
        reserved_up=reserved_up,
        reserved_dn=reserved_dn,
    )
