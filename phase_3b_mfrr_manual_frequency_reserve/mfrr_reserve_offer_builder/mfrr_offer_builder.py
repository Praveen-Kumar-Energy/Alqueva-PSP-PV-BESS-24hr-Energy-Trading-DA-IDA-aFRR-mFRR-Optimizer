"""
mfrr_offer_builder.py — build the mFRR capacity offer from headroom aFRR did not take.

mFRR = manual Frequency Restoration Reserve (FAT 12.5 min, MARI). It is slower and
lower-value than aFRR, so it is sized from the headroom REMAINING after the aFRR
commitment (passed in as reserved_up/dn). The offer is further limited to a
fraction of headroom (config mfrr.max_offer_fraction) to leave operating margin.
"""
from __future__ import annotations

from typing import Dict

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model.reserve_offer_builder import (
    build_reserve_offers, ReserveOffer,
)


def build_mfrr_offers(committed_net: Dict[int, float],
                      cap_up: Dict[int, float], cap_dn: Dict[int, float],
                      reserved_up: Dict[int, float], reserved_dn: Dict[int, float],
                      cfg: AppConfig) -> Dict[int, ReserveOffer]:
    mf = cfg.market.mfrr
    return build_reserve_offers(
        product="mFRR",
        committed_net=committed_net,
        cap_prices_up=cap_up,
        cap_prices_dn=cap_dn,
        cfg=cfg,
        fat_min=mf.fat_min,                          # 12.5 min
        max_up_mw=cfg.market.afrr.max_offer_up_mw,   # mFRR shares the aFRR market-size cap; no separate config field
        max_dn_mw=cfg.market.afrr.max_offer_dn_mw,
        headroom_fraction=mf.max_offer_fraction,     # 0.20 of remaining headroom
        reserved_up=reserved_up,                     # subtract aFRR commitment
        reserved_dn=reserved_dn,
    )
