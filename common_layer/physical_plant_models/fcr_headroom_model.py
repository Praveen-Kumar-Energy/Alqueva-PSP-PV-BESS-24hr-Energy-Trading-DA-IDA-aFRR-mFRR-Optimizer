"""
fcr_headroom_model.py — FCR mandatory headroom (NON-tradable).

FCR (Frequency Containment Reserve / primary control) is MANDATORY and
NON-REMUNERATED in Portugal and Spain: generators must provide it as a grid-code
obligation, not sell it on a market. REN: Manual de Procedimentos do Gestor do
Sistema. https://www.ren.pt

Consequences enforced here (spec INV-7):
  * FCR is modelled ONLY as reserved power headroom subtracted from the plant
    envelope before any energy or reserve (aFRR/mFRR) offer is sized.
  * There is NO FCR market gate, NO FCR price, NO FCR bid anywhere in the system.

If REN assigns an explicit obligation, set plant.fcr.mandatory_headroom_mw > 0;
otherwise it is 0 and this model is a no-op that still documents the rule.
"""
from __future__ import annotations

from common_layer.configuration.plant_config import FCRConfig


class FCRHeadroomModel:
    def __init__(self, cfg: FCRConfig):
        self.cfg = cfg

    @property
    def reserved_mw(self) -> float:
        """MW that must be kept free for FCR on both up and down regulation."""
        return max(0.0, self.cfg.mandatory_headroom_mw)

    @property
    def is_tradable(self) -> bool:
        """Always False — FCR is never a market product in PT/ES (INV-7)."""
        return False

    def usable_generation_mw(self, p_max_generation_mw: float) -> float:
        """Generation envelope after reserving FCR up-headroom."""
        return p_max_generation_mw - self.reserved_mw

    def usable_pump_mw(self, p_max_pump_mw: float) -> float:
        """Pump (demand) envelope after reserving FCR down-headroom."""
        return p_max_pump_mw - self.reserved_mw
