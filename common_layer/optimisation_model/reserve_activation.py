"""
reserve_activation.py — shared TSO-activation simulation for aFRR / mFRR.

During delivery the TSO calls part of the offered reserve in some ISPs. This
engine, shared by Phase 4B (aFRR) and 4C (mFRR):
  * reads the committed offer (ReserveStore) for the product,
  * reads the scheduled net (DeliveryStore) per ISP for physical headroom check,
  * simulates which ISPs are activated using a correlated hold-state machine
    (never both up AND down in the same ISP — mutually exclusive),
  * tracks BESS SOC depletion so availability shrinks after sustained activations,
  * enforces physical headroom: scheduled_net ± activated ≤ plant envelope,
  * stores SEPARATE up_price and dn_price per activation row for correct settlement,
  * logs activated MW + energy prices per ISP (ActivationStore) for settlement.

aFRR is called more often and shallower (continuous AGC); mFRR is called less
often and deeper (discrete TSO instruction) — captured by per-product profiles.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List

from common_layer.configuration.config_loader import AppConfig
from common_layer.utilities import date_utils as du
from common_layer.database import ReserveStore, ActivationStore, DeliveryStore
from common_layer.optimisation_model.reserve_offer_builder import (
    fat_deliverable_mw, fat_deliverable_dn_mw,
)
from common_layer.optimisation_model.activation_ramp_tracker import effective_isp_hours
from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.da_price_forecaster import (
    forecast_da_prices,
)

# Per-product activation behaviour.
_PROFILE = {
    "aFRR": {
        "p_up":   0.35,   # probability an ISP starts a NEW up activation
        "p_dn":   0.30,   # probability an ISP starts a NEW down activation
        "depth":  0.45,   # fraction of offered MW called by TSO
        "min_hold_isps": 2,    # minimum consecutive ISPs in activated state (correlated AGC)
        "min_activate_mw": 1.0,
    },
    "mFRR": {
        "p_up":   0.12,
        "p_dn":   0.10,
        "depth":  0.80,
        "min_hold_isps": 3,    # mFRR holds longer (discrete TSO instruction)
        "min_activate_mw": 2.0,
    },
}

_DIR_NONE = 0
_DIR_UP   = 1
_DIR_DN   = -1


@dataclass
class ActivationSummary:
    product: str
    n_isp_activated: int
    up_mwh: float
    dn_mwh: float
    rows: List[dict]


def simulate_and_log_activation(product: str, delivery_date: str, cfg: AppConfig,
                                fat_min: float) -> ActivationSummary:
    offers = ReserveStore().load_reserve(delivery_date, product)
    if not offers:
        return ActivationSummary(product, 0, 0.0, 0.0, [])

    # Load scheduled net per ISP — needed to check physical headroom at activation time.
    delivery_rows = DeliveryStore().load(delivery_date)
    scheduled_by_isp: Dict[int, float] = {r["isp"]: r["scheduled_mw"] for r in delivery_rows}

    day = du.parse_date(delivery_date)
    isp_duration_min = du.isp_duration_min(day)
    isp_h = isp_duration_min / 60.0
    # Ramp-corrected effective ISP hours: accounts for linear ramp-up within FAT
    # rather than crediting full MW from t=0. aFRR: 0.2083 h, mFRR: 0.1458 h.
    eff_isp_h = effective_isp_hours(fat_min, isp_duration_min)
    hours = sorted(offers)
    da = forecast_da_prices(hours, delivery_date)

    p_gen_cap = cfg.plant.p_max_generation_mw
    p_pump_cap = cfg.plant.p_max_pump_mw

    # BESS SOC tracking — initialize from plant config; depletes/charges each ISP.
    bess_cap_mwh = cfg.plant.bess.capacity_mwh
    bess_soc_mwh = cfg.plant.bess.initial_soc_frac * bess_cap_mwh
    bess_soc_min = cfg.plant.bess.e_min_mwh
    bess_soc_max = cfg.plant.bess.e_max_mwh
    bess_power_mw = cfg.plant.bess.power_mw

    prof = _PROFILE.get(product, _PROFILE["aFRR"])
    rng = random.Random(f"act-{product}-{delivery_date}")

    rows: List[dict] = []
    up_mwh = dn_mwh = 0.0

    # Correlated hold state machine: ensures each activation persists for
    # >= min_hold_isps consecutive ISPs and direction is mutually exclusive.
    current_dir = _DIR_NONE
    hold_remaining = 0

    for h in hours:
        off = offers[h]
        # Separate activation prices per direction — up and down settle at different rates.
        # aFRR: ±25% of DA (continuous AGC, tight to DA).
        # mFRR: ±30% of DA (discrete TSO instruction, higher premium).
        # Capped at realistic MIBEL regulatory limits: 200 EUR/MWh up, floor 0.
        if product == "aFRR":
            up_price = round(min(da[h] * 1.25, 200.0), 2)
            dn_price = round(max(da[h] * 0.75, 0.0), 2)
        else:  # mFRR
            up_price = round(min(da[h] * 1.30, 150.0), 2)
            dn_price = round(max(da[h] * 0.70, 0.0), 2)

        for isp in du.hour_to_isps(h, day):
            sched = scheduled_by_isp.get(isp, 0.0)

            # BESS available power depends on current SOC — drops to zero at limits.
            bess_up_avail = bess_power_mw if bess_soc_mwh > bess_soc_min + 1e-6 else 0.0
            bess_dn_avail = bess_power_mw if bess_soc_mwh < bess_soc_max - 1e-6 else 0.0

            # Mode-aware FAT cap: in pump mode with short FAT, crossing to generation
            # is not guaranteed; up deliverable limited to ramp-to-zero + BESS.
            fat_up = fat_deliverable_mw(cfg, fat_min, current_net_mw=sched)
            fat_up = fat_up - bess_power_mw + bess_up_avail   # swap BESS term for SOC-aware
            fat_dn = fat_deliverable_dn_mw(cfg, fat_min)
            fat_dn = fat_dn - bess_power_mw + bess_dn_avail   # swap BESS term for SOC-aware

            # Physical headroom from actual scheduled net (not offer size) — prevents
            # activating more than the plant can physically deliver in this ISP.
            headroom_up = max(0.0, p_gen_cap - sched)
            headroom_dn = max(0.0, sched + p_pump_cap)

            # Hold state: once activated, direction is held for min_hold_isps ISPs.
            # A new roll only happens after the hold expires.
            if hold_remaining > 0:
                direction = current_dir
                hold_remaining -= 1
            else:
                r1 = rng.random()
                if r1 < prof["p_up"]:
                    direction = _DIR_UP
                    hold_remaining = prof["min_hold_isps"] - 1
                elif r1 < prof["p_up"] + prof["p_dn"]:
                    # Sequential else-if ensures UP and DN never both trigger.
                    direction = _DIR_DN
                    hold_remaining = prof["min_hold_isps"] - 1
                else:
                    direction = _DIR_NONE
                current_dir = direction

            if direction == _DIR_NONE:
                continue

            if direction == _DIR_UP:
                up = min(off["up_mw"] * prof["depth"], fat_up, headroom_up, off["up_mw"])
                dn = 0.0
            else:
                up = 0.0
                dn = min(off["dn_mw"] * prof["depth"], fat_dn, headroom_dn, off["dn_mw"])

            # Minimum activation threshold (very small calls are not issued).
            if up < prof["min_activate_mw"] and dn < prof["min_activate_mw"]:
                continue

            # Update BESS SOC after this ISP's activation; clamp to configured limits.
            if up > 0:
                bess_contrib = min(bess_up_avail, up)
                bess_soc_mwh = max(bess_soc_min, bess_soc_mwh - bess_contrib * isp_h)
            elif dn > 0:
                bess_contrib = min(bess_dn_avail, dn)
                bess_soc_mwh = min(bess_soc_max, bess_soc_mwh + bess_contrib * isp_h)

            # Energy credited uses ramp-corrected hours, not face ISP duration.
            up_mwh += up * eff_isp_h
            dn_mwh += dn * eff_isp_h
            rows.append({
                "isp":  isp,
                "hour": h,
                "up_mw":             up,
                "dn_mw":             dn,
                "up_price_eur_mwh":  up_price,
                "dn_price_eur_mwh":  dn_price,
                "eff_isp_h":         eff_isp_h,  # stored for settlement accuracy
            })

    ActivationStore().save(delivery_date, product, rows)
    return ActivationSummary(product, len(rows), up_mwh, dn_mwh, rows)
