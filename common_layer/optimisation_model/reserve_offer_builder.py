"""
reserve_offer_builder.py — shared aFRR / mFRR capacity-offer sizing + checker.

Reserve is offered from the headroom LEFT AFTER the energy position is committed
(sequential approach: energy first, then reserve from what remains). This makes
the no-double-sell rule true by construction; the checker re-verifies it.

For each delivery hour with committed net N (+ generation, - pump):
    up headroom   = P_gen_cap  - N      (room to push output UP)
    down headroom = N + P_pump_cap      (room to pull output DOWN)
Both are capped by:
    * FAT deliverability  = plant ramp/min * FAT minutes (+ BESS power, instant)
      (hydro ramps fast, so the energy headroom usually binds; mode changes are
       excluded — they are slower than the aFRR FAT),
    * the market product cap (max_offer_up/dn, or mFRR fraction of headroom).

Spec mapping:
    PR-11  offer_up + N <= P_gen_cap ;  N - offer_dn >= -P_pump_cap  (no MW sold twice)
    PR-12  offer <= FAT-deliverable capacity
    INV-6  energy +/- reserve stays inside the plant envelope
    INV-7  FCR headroom already removed from P_gen_cap / P_pump_cap
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from common_layer.configuration.config_loader import AppConfig

EPS = 1e-6

# aFRR FAT 5 min: Francis reversible units can ramp within mode within 5 min,
# but a full pump-to-generation MODE SWITCH (stop pump, start turbine) takes
# ~4-7 min in practice — borderline for aFRR, safe for mFRR 12.5 min.
# We set a conservative threshold: mode switches NOT guaranteed for aFRR FAT.
_AFRR_FAT_MODE_SWITCH_MIN = 5.0   # minutes — aFRR FAT (borderline)
_MFRR_FAT_MODE_SWITCH_MIN = 12.5  # minutes — mFRR FAT (mode switch feasible)
_MIN_SAFE_MODE_SWITCH_MIN = 8.0   # conservative: only allow switch if FAT >= 8 min


@dataclass
class ReserveOffer:
    hour: int
    up_mw: float
    dn_mw: float
    cap_price_up_eur_mw: float
    cap_price_dn_eur_mw: float


class ReserveCheckError(ValueError):
    """Raised when a reserve offer would breach the headroom envelope."""


def _envelope(cfg: AppConfig) -> tuple[float, float]:
    """(P_gen_cap, P_pump_cap) after FCR headroom removal (INV-7)."""
    p = cfg.plant
    fcr = max(0.0, p.fcr.mandatory_headroom_mw)
    return p.p_max_generation_mw - fcr, p.p_max_pump_mw - fcr


def fat_deliverable_mw(cfg: AppConfig, fat_min: float,
                        current_net_mw: Optional[float] = None,
                        pv_available_mw: float = 0.0) -> float:
    """Max MW ramp UP the plant can deliver within the product FAT.

    Mode-aware: in pump mode (current_net_mw < 0) with short FAT
    (< _MIN_SAFE_MODE_SWITCH_MIN), a full pump-to-generation mode switch cannot
    be guaranteed. In that case the up deliverable is limited to reducing pumping
    to zero plus BESS — the plant cannot cross the pump/generation boundary.

    PV-flag gating: BESS upward FRR contribution is gated by PV availability.
    When PV is generating (pv_available_mw >= 0.01 MW), BESS charges from PV
    and can provide upward FRR by discharging. When PV is unavailable (night),
    BESS upward contribution is excluded from the offer.

    For backward compatibility, passing current_net_mw=None assumes generation
    mode (the conservatively correct assumption for offer sizing when per-hour
    mode is not passed).
    """
    psp_ramp_cap = cfg.plant.psp.total_ramp_mw_per_min * fat_min
    # BESS upward FRR only when PV is available (PV-flag gating from original model)
    pv_flag = 1 if pv_available_mw >= 0.01 else 0
    bess_cap = cfg.plant.bess.power_mw * pv_flag

    if current_net_mw is not None and current_net_mw < 0 and fat_min < _MIN_SAFE_MODE_SWITCH_MIN:
        # Pump mode, short FAT: can only ramp pump to zero, not start generating.
        pump_magnitude = abs(current_net_mw)
        up_from_pump_ramp = min(pump_magnitude, psp_ramp_cap)
        return up_from_pump_ramp + bess_cap

    return psp_ramp_cap + bess_cap


def fat_deliverable_dn_mw(cfg: AppConfig, fat_min: float) -> float:
    """Max MW ramp DOWN (increasing pump or reducing generation) within FAT.

    Down direction is symmetric — ramping down generation or ramping up pumping
    does not require a mode switch, so the standard formula applies regardless
    of starting mode.
    """
    return cfg.plant.psp.total_ramp_mw_per_min * fat_min + cfg.plant.bess.power_mw


def build_reserve_offers(
    product: str,
    committed_net: Dict[int, float],
    cap_prices_up: Dict[int, float],
    cap_prices_dn: Dict[int, float],
    cfg: AppConfig,
    fat_min: float,
    max_up_mw: float,
    max_dn_mw: float,
    headroom_fraction: float = 1.0,
    reserved_up: Optional[Dict[int, float]] = None,
    reserved_dn: Optional[Dict[int, float]] = None,
    pv_available_mw: Optional[Dict[int, float]] = None,
) -> Dict[int, ReserveOffer]:
    """Size up/down reserve offers per hour from leftover headroom.

    headroom_fraction < 1 (e.g. mFRR 0.20) keeps a margin and avoids committing
    the entire envelope to a single, slower reserve product. reserved_up/dn are
    MW already committed to a higher-priority product (e.g. aFRR before mFRR) and
    are subtracted from the available headroom so no MW is offered to two
    products (PR-11 across products).

    FAT is applied per-hour using the committed net to determine operating mode,
    so pump-mode hours get the correct (reduced) up deliverable for short FATs.
    """
    p_gen_cap, p_pump_cap = _envelope(cfg)
    fat_dn = fat_deliverable_dn_mw(cfg, fat_min)
    reserved_up = reserved_up or {}
    reserved_dn = reserved_dn or {}
    pv_available_mw = pv_available_mw or {}

    offers: Dict[int, ReserveOffer] = {}
    for h, n in committed_net.items():
        # Mode-aware + PV-flag-gated FAT cap per hour.
        pv_mw = pv_available_mw.get(h, 0.0)
        fat_up = fat_deliverable_mw(cfg, fat_min, current_net_mw=n, pv_available_mw=pv_mw)
        up_headroom = max(0.0, p_gen_cap - n - reserved_up.get(h, 0.0))
        dn_headroom = max(0.0, n + p_pump_cap - reserved_dn.get(h, 0.0))
        up = min(up_headroom * headroom_fraction, fat_up, max_up_mw)
        dn = min(dn_headroom * headroom_fraction, fat_dn, max_dn_mw)
        # Keep full precision: rounding here could push the offer a hair above the
        # true headroom and trip the PR-11 envelope check. Display rounds instead.
        offers[h] = ReserveOffer(
            hour=h,
            up_mw=up,
            dn_mw=dn,
            cap_price_up_eur_mw=cap_prices_up.get(h, 0.0),
            cap_price_dn_eur_mw=cap_prices_dn.get(h, 0.0),
        )
    return offers


def check_reserve_offers(
    offers: Dict[int, ReserveOffer],
    committed_net: Dict[int, float],
    cfg: AppConfig,
    fat_min: float,
    product: str = "aFRR",
    cap_price_max: Optional[float] = None,
    reserved_up: Optional[Dict[int, float]] = None,
    reserved_dn: Optional[Dict[int, float]] = None,
    pv_available_mw: Optional[Dict[int, float]] = None,
) -> List[str]:
    """Phase 3A reserve checker. Returns [] if clean; raises ReserveCheckError.

    reserved_up/dn = MW already committed to a higher-priority product; this
    offer plus that prior commitment plus the energy position must fit the
    envelope (PR-11 across products). FAT check uses mode-aware per-hour cap.
    """
    p_gen_cap, p_pump_cap = _envelope(cfg)
    fat_dn = fat_deliverable_dn_mw(cfg, fat_min)
    reserved_up = reserved_up or {}
    reserved_dn = reserved_dn or {}
    v: List[str] = []

    for h, off in offers.items():
        n = committed_net.get(h, 0.0)
        ru, rd = reserved_up.get(h, 0.0), reserved_dn.get(h, 0.0)
        # PV flag: if pv_available_mw not passed, assume BESS always counts (backward compat).
        pv_mw = (pv_available_mw.get(h, 0.0) if pv_available_mw is not None
                 else cfg.plant.bess.power_mw)
        fat_up = fat_deliverable_mw(cfg, fat_min, current_net_mw=n, pv_available_mw=pv_mw)
        # PR-11 / INV-6: energy + ALL reserve (prior + this) must fit the envelope.
        if n + ru + off.up_mw > p_gen_cap + EPS:
            v.append(f"H{h} PR-11 up {off.up_mw:.2f} + prior {ru:.2f} + energy {n:.2f} "
                     f"= {n + ru + off.up_mw:.2f} > gen cap {p_gen_cap:.2f} MW")
        if n - rd - off.dn_mw < -p_pump_cap - EPS:
            v.append(f"H{h} PR-11 energy {n:.2f} - prior {rd:.2f} - dn {off.dn_mw:.2f} "
                     f"= {n - rd - off.dn_mw:.2f} < -pump cap {-p_pump_cap:.2f} MW")
        # PR-12: within FAT deliverability (mode-aware for up).
        if off.up_mw > fat_up + EPS:
            v.append(f"H{h} PR-12 up {off.up_mw:.2f} > mode-aware FAT-deliverable "
                     f"{fat_up:.2f} MW (net={n:.1f} MW)")
        if off.dn_mw > fat_dn + EPS:
            v.append(f"H{h} PR-12 dn {off.dn_mw:.2f} > FAT-deliverable {fat_dn:.2f} MW")
        # non-negative
        if off.up_mw < -EPS or off.dn_mw < -EPS:
            v.append(f"H{h} negative reserve: up={off.up_mw}, dn={off.dn_mw}")
        # price cap (aFRR REN cap 250 EUR/MW)
        if cap_price_max is not None:
            if off.cap_price_up_eur_mw > cap_price_max + EPS:
                v.append(f"H{h} up cap price {off.cap_price_up_eur_mw} > max {cap_price_max} EUR/MW")
            if off.cap_price_dn_eur_mw > cap_price_max + EPS:
                v.append(f"H{h} dn cap price {off.cap_price_dn_eur_mw} > max {cap_price_max} EUR/MW")

    if v:
        raise ReserveCheckError(
            f"[{product}] reserve checker found {len(v)} violation(s):\n  - "
            + "\n  - ".join(v))
    return v


def check_combined_activation_headroom(
    delivery_date: str,
    cfg: AppConfig,
    products: List[str] = None,
) -> List[str]:
    """Post-delivery check: combined aFRR+mFRR activations must not exceed plant limits.

    Reads DeliveryStore (scheduled net per ISP) and ActivationStore (activated
    MW per ISP per product) and verifies that:
        scheduled + sum(up_activations) <= p_gen_cap
        scheduled - sum(dn_activations) >= -p_pump_cap

    Returns a list of violation strings (empty if clean).
    """
    from common_layer.database import DeliveryStore, ActivationStore  # local import avoids cycle

    if products is None:
        products = ["aFRR", "mFRR"]

    p_gen_cap = cfg.plant.p_max_generation_mw
    p_pump_cap = cfg.plant.p_max_pump_mw

    delivery = {r["isp"]: r["scheduled_mw"] for r in DeliveryStore().load(delivery_date)}
    act_store = ActivationStore()

    combined_up: Dict[int, float] = {}
    combined_dn: Dict[int, float] = {}
    for product in products:
        for a in act_store.load(delivery_date, product):
            isp = a["isp"]
            combined_up[isp] = combined_up.get(isp, 0.0) + a["up_mw"]
            combined_dn[isp] = combined_dn.get(isp, 0.0) + a["dn_mw"]

    violations: List[str] = []
    for isp, sched in delivery.items():
        total_up = combined_up.get(isp, 0.0)
        total_dn = combined_dn.get(isp, 0.0)
        if total_up > EPS and sched + total_up > p_gen_cap + EPS:
            violations.append(
                f"ISP{isp} combined activation: sched {sched:.1f} + up {total_up:.1f} "
                f"= {sched + total_up:.1f} > gen cap {p_gen_cap:.1f} MW")
        if total_dn > EPS and sched - total_dn < -p_pump_cap - EPS:
            violations.append(
                f"ISP{isp} combined activation: sched {sched:.1f} - dn {total_dn:.1f} "
                f"= {sched - total_dn:.1f} < -pump cap {-p_pump_cap:.1f} MW")

    return violations
