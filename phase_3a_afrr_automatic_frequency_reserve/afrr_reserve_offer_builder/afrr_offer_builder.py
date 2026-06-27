"""
afrr_offer_builder.py — build the aFRR capacity offer from leftover headroom.

aFRR is the FAST automatic reserve (FAT 5 min, restores frequency within the
+/- 0.200 Hz band, 49.800-50.200 Hz). It has first call on the plant's headroom
(higher value than mFRR). Offers are bounded by the market max (config
afrr.max_offer_up/dn_mw) and FAT deliverability, and sized via the shared builder.
"""
from __future__ import annotations

from typing import Dict, Tuple

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model.reserve_offer_builder import (
    build_reserve_offers, ReserveOffer,
)


def build_afrr_offers(committed_net: Dict[int, float],
                      cap_up: Dict[int, float], cap_dn: Dict[int, float],
                      cfg: AppConfig) -> Dict[int, ReserveOffer]:
    a = cfg.market.afrr
    return build_reserve_offers(
        product="aFRR",
        committed_net=committed_net,
        cap_prices_up=cap_up,
        cap_prices_dn=cap_dn,
        cfg=cfg,
        fat_min=a.fat_min,                 # 5 min
        max_up_mw=a.max_offer_up_mw,
        max_dn_mw=a.max_offer_dn_mw,
        headroom_fraction=1.0,             # aFRR has first call on headroom
    )
