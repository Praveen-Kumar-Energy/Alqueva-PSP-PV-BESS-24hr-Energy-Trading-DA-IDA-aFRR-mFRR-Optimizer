"""
afrr_offer_checker.py — Phase 3A aFRR offer validity checker.

Thin binding to the shared reserve checker: verifies the aFRR offer fits the
headroom envelope around the committed energy position (PR-11), is FAT-deliverable
(PR-12), and prices stay under the REN cap (250 EUR/MW). Raises ReserveCheckError
on any violation so nothing is offered that the plant cannot honour.
"""
from __future__ import annotations

from typing import Dict, List

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model.reserve_offer_builder import (
    check_reserve_offers, ReserveOffer,
)


def check_afrr_offers(offers: Dict[int, ReserveOffer], committed_net: Dict[int, float],
                      cfg: AppConfig) -> List[str]:
    return check_reserve_offers(
        offers=offers,
        committed_net=committed_net,
        cfg=cfg,
        fat_min=cfg.market.afrr.fat_min,
        product="aFRR",
        cap_price_max=cfg.market.afrr.cap_price_max_eur_mw,
    )
