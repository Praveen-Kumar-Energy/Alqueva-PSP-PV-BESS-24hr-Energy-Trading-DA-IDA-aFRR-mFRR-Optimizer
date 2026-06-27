"""
operational_analytics.py — post-solve operational and temporal analytics.

Four analytics functions derived from the original research code (PSP+PV+BESS
V2.py), adapted for the industrial two-reservoir sequential pipeline.

Functions:
    compute_operational_patterns  — run durations, starts, peak-hour activity
    compute_temporal_patterns     — morning/afternoon/evening/night splits
    compute_economic_kpis_extended — capacity factors, efficiency %, DA/FRR split
    compute_frr_strategy_metrics  — aFRR/mFRR offer summary, BESS PV-gated hours

All functions take plain dicts from GateResults — no Pyomo model dependency.
"""
from __future__ import annotations

from typing import Dict, List, Optional


# ── Operational pattern metrics ───────────────────────────────────────────────

def compute_operational_patterns(
    psp_schedule: Dict[int, dict],
    bess_schedule: Dict[int, dict],
    da_prices: Dict[int, float],
) -> dict:
    """Run durations, starts, peak/off-peak operation from the solved schedule.

    Args:
        psp_schedule: {h: {turbine_mw, pump_mw, units_on_turb, units_on_pump}}
        bess_schedule: {h: {charge_mw, discharge_mw, soc_mwh}}
        da_prices:    {h: EUR/MWh}

    Returns dict of operational KPIs.
    """
    H = sorted(psp_schedule.keys())
    prices = [da_prices[h] for h in H]
    price_q75 = sorted(prices)[int(0.75 * len(prices))]
    price_q25 = sorted(prices)[int(0.25 * len(prices))]

    # Per-hour aggregate turbine/pump on-status (any unit on = hour active)
    turb_on  = {h: int(any(psp_schedule[h]["units_on_turb"])) for h in H}
    pump_on  = {h: int(any(psp_schedule[h]["units_on_pump"])) for h in H}
    n_turb   = {h: sum(psp_schedule[h]["units_on_turb"]) for h in H}
    n_pump   = {h: sum(psp_schedule[h]["units_on_pump"]) for h in H}

    # Run duration sequences
    turb_runs = _run_lengths([turb_on[h] for h in H])
    pump_runs = _run_lengths([pump_on[h] for h in H])

    # Unit starts (0→1 transitions)
    turb_starts = sum(
        sum(1 for u_idx in range(len(psp_schedule[h]["units_on_turb"]))
            if psp_schedule[h]["units_on_turb"][u_idx] == 1 and
            (h == H[0] or psp_schedule[H[H.index(h)-1]]["units_on_turb"][u_idx] == 0))
        for h in H
    )
    pump_starts = sum(
        sum(1 for u_idx in range(len(psp_schedule[h]["units_on_pump"]))
            if psp_schedule[h]["units_on_pump"][u_idx] == 1 and
            (h == H[0] or psp_schedule[H[H.index(h)-1]]["units_on_pump"][u_idx] == 0))
        for h in H
    )

    # Peak/off-peak operation
    turb_peak    = sum(1 for h in H if turb_on[h] and da_prices[h] >= price_q75)
    turb_offpeak = sum(1 for h in H if turb_on[h] and da_prices[h] <= price_q25)
    pump_peak    = sum(1 for h in H if pump_on[h] and da_prices[h] >= price_q75)
    pump_offpeak = sum(1 for h in H if pump_on[h] and da_prices[h] <= price_q25)

    # BESS cycles (charge→discharge transitions)
    bess_chg_on = [1 if bess_schedule[h]["charge_mw"] > 0.01 else 0 for h in H]
    bess_dis_on = [1 if bess_schedule[h]["discharge_mw"] > 0.01 else 0 for h in H]

    return {
        "turbine_hours_total": sum(turb_on.values()),
        "pump_hours_total": sum(pump_on.values()),
        "turbine_starts_total": turb_starts,
        "pump_starts_total": pump_starts,
        "turb_avg_run_h": round(sum(turb_runs) / len(turb_runs), 2) if turb_runs else 0.0,
        "turb_max_run_h": max(turb_runs) if turb_runs else 0,
        "pump_avg_run_h": round(sum(pump_runs) / len(pump_runs), 2) if pump_runs else 0.0,
        "pump_max_run_h": max(pump_runs) if pump_runs else 0,
        "turb_hours_top25pct_price": turb_peak,
        "turb_hours_bot25pct_price": turb_offpeak,
        "pump_hours_top25pct_price": pump_peak,
        "pump_hours_bot25pct_price": pump_offpeak,
        "bess_charge_hours": sum(bess_chg_on),
        "bess_discharge_hours": sum(bess_dis_on),
        "avg_units_turbining": round(sum(n_turb.values()) / len(H), 2),
        "avg_units_pumping": round(sum(n_pump.values()) / len(H), 2),
    }


def _run_lengths(sequence: List[int]) -> List[int]:
    """Return list of consecutive-1 run lengths in a binary sequence."""
    runs, current = [], 0
    for v in sequence:
        if v == 1:
            current += 1
        elif current > 0:
            runs.append(current)
            current = 0
    if current > 0:
        runs.append(current)
    return runs


# ── Temporal pattern metrics (hour-of-day) ───────────────────────────────────

def compute_temporal_patterns(
    psp_schedule: Dict[int, dict],
    pv_schedule: Dict[int, dict],
    da_prices: Dict[int, float],
) -> dict:
    """Morning/afternoon/evening/night operational splits.

    Hour bands (CET): night 0-5, morning 6-11, afternoon 12-17, evening 18-23.
    Hours are 1-indexed (1=00:00, 24=23:00) so band offsets shift by 1.

    Returns dict with turbine%, pump%, avg_power, avg_profit per band.
    """
    H = sorted(psp_schedule.keys())

    bands = {
        "night":     [h for h in H if (h - 1) % 24 in range(0, 6)],
        "morning":   [h for h in H if (h - 1) % 24 in range(6, 12)],
        "afternoon": [h for h in H if (h - 1) % 24 in range(12, 18)],
        "evening":   [h for h in H if (h - 1) % 24 in range(18, 24)],
    }

    result: dict = {}
    for band, hrs in bands.items():
        if not hrs:
            result[band] = {}
            continue
        n = len(hrs)
        turb_h  = sum(1 for h in hrs if any(psp_schedule[h]["units_on_turb"]))
        pump_h  = sum(1 for h in hrs if any(psp_schedule[h]["units_on_pump"]))
        avg_pwr = sum(psp_schedule[h]["turbine_mw"] - psp_schedule[h]["pump_mw"]
                      for h in hrs) / n
        avg_pft = sum(da_prices[h] * (psp_schedule[h]["turbine_mw"]
                                      - psp_schedule[h]["pump_mw"]) for h in hrs) / n
        avg_px  = sum(da_prices[h] for h in hrs) / n
        result[band] = {
            "hours": n,
            "turbine_pct": round(100.0 * turb_h / n, 1),
            "pump_pct":    round(100.0 * pump_h / n, 1),
            "avg_net_mw":  round(avg_pwr, 2),
            "avg_profit_eur_h": round(avg_pft, 2),
            "avg_price_eur_mwh": round(avg_px, 2),
        }

    # Peak turbine and peak profit hour of day
    net_pwr = {h: psp_schedule[h]["turbine_mw"] - psp_schedule[h]["pump_mw"] for h in H}
    profit  = {h: da_prices[h] * net_pwr[h] for h in H}
    result["peak_turbine_hour"]     = max(H, key=lambda h: psp_schedule[h]["turbine_mw"])
    result["peak_profit_hour"]      = max(H, key=lambda h: profit[h])
    result["peak_pv_hour"]          = max(H, key=lambda h: pv_schedule[h]["available_mw"])
    result["price_turb_correlation"] = _correlation(
        [da_prices[h] for h in H],
        [psp_schedule[h]["turbine_mw"] for h in H],
    )
    return result


def _correlation(x: List[float], y: List[float]) -> float:
    """Pearson correlation coefficient, returns 0.0 if no variance."""
    n = len(x)
    if n < 2:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    dx  = sum((xi - mx) ** 2 for xi in x) ** 0.5
    dy  = sum((yi - my) ** 2 for yi in y) ** 0.5
    return round(num / (dx * dy), 4) if dx * dy > 1e-12 else 0.0


# ── Extended economic KPIs ────────────────────────────────────────────────────

def compute_economic_kpis_extended(
    psp_schedule: Dict[int, dict],
    bess_schedule: Dict[int, dict],
    pv_schedule: Dict[int, dict],
    reservoir_trajectory: Dict[int, dict],
    efficiency_per_hour: Dict[int, dict],
    da_prices: Dict[int, float],
    energy_revenue_eur: float,
    reserve_revenue_eur: float,
    p_turbine_max_mw: float,
    p_pump_max_mw: float,
    bess_power_mw: float,
    upper_usable_hm3: float,
    upper_min_hm3: float,
    dt_h: float = 1.0,
) -> dict:
    """Extended KPIs: capacity factors, efficiency, reservoir fill, revenue split.

    Args:
        p_turbine_max_mw: nameplate turbine capacity (all units, MW)
        p_pump_max_mw:    nameplate pump capacity (all units, MW)
        bess_power_mw:    BESS rated power (MW)
        upper_usable_hm3: usable upper reservoir range (hm³)
        upper_min_hm3:    minimum upper reservoir level (hm³)
    """
    H = sorted(psp_schedule.keys())
    n = len(H)

    turb_mwh  = sum(psp_schedule[h]["turbine_mw"] * dt_h for h in H)
    pump_mwh  = sum(psp_schedule[h]["pump_mw"]    * dt_h for h in H)
    pv_mwh    = sum(pv_schedule[h]["used_mw"]     * dt_h for h in H)
    bess_mwh  = sum(bess_schedule[h]["discharge_mw"] * dt_h for h in H)
    pv_av_mwh = sum(pv_schedule[h]["available_mw"]   * dt_h for h in H)

    cf_turb = (turb_mwh / (p_turbine_max_mw * n * dt_h)) * 100.0 if p_turbine_max_mw > 0 else 0.0
    cf_pump = (pump_mwh / (p_pump_max_mw    * n * dt_h)) * 100.0 if p_pump_max_mw > 0 else 0.0
    cf_bess = (bess_mwh / (bess_power_mw    * n * dt_h)) * 100.0 if bess_power_mw > 0 else 0.0
    pv_util = (pv_mwh  / pv_av_mwh) * 100.0 if pv_av_mwh > 1e-6 else 0.0

    # Average operating efficiency (only hours when operating)
    eta_trb_vals = [efficiency_per_hour[h]["eta_trb_pw"] for h in H
                    if efficiency_per_hour[h]["eta_trb_pw"] > 1e-6]
    eta_pmp_vals = [efficiency_per_hour[h]["eta_pmp_pw"] for h in H
                    if efficiency_per_hour[h]["eta_pmp_pw"] > 1e-6]
    avg_eta_trb = sum(eta_trb_vals) / len(eta_trb_vals) if eta_trb_vals else 0.0
    avg_eta_pmp = sum(eta_pmp_vals) / len(eta_pmp_vals) if eta_pmp_vals else 0.0

    # Reservoir fill level at end of day
    end_vol = reservoir_trajectory[H[-1]]["upper_hm3"]
    usable_range = upper_usable_hm3 - upper_min_hm3
    reservoir_fill_pct = ((end_vol - upper_min_hm3) / usable_range * 100.0
                          if usable_range > 0 else 0.0)

    # Head range over the day
    heads = [reservoir_trajectory[h]["head_m"] for h in H]
    head_min = min(heads)
    head_max = max(heads)

    # DA/FRR revenue split
    total_rev = energy_revenue_eur + reserve_revenue_eur
    da_share  = 100.0 * energy_revenue_eur / total_rev if total_rev > 1e-6 else 0.0
    frr_share = 100.0 * reserve_revenue_eur / total_rev if total_rev > 1e-6 else 0.0

    return {
        "turbine_capacity_factor_pct": round(cf_turb, 2),
        "pump_capacity_factor_pct":    round(cf_pump, 2),
        "bess_discharge_cf_pct":       round(cf_bess, 2),
        "pv_utilisation_pct":          round(pv_util, 2),
        "avg_turbine_efficiency_pct":  round(avg_eta_trb * 100.0, 2),
        "avg_pump_efficiency_pct":     round(avg_eta_pmp * 100.0, 2),
        "reservoir_fill_end_pct":      round(reservoir_fill_pct, 2),
        "head_min_m":                  round(head_min, 2),
        "head_max_m":                  round(head_max, 2),
        "head_range_m":                round(head_max - head_min, 2),
        "da_revenue_share_pct":        round(da_share, 1),
        "frr_revenue_share_pct":       round(frr_share, 1),
        "energy_revenue_eur":          round(energy_revenue_eur, 2),
        "reserve_revenue_eur":         round(reserve_revenue_eur, 2),
    }


# ── FRR strategy metrics ──────────────────────────────────────────────────────

def compute_frr_strategy_metrics(
    afrr_offers: Optional[Dict[int, dict]] = None,
    mfrr_offers: Optional[Dict[int, dict]] = None,
    pv_schedule: Optional[Dict[int, dict]] = None,
    bess_power_mw: float = 1.0,
) -> dict:
    """FRR offer summary per product and BESS PV-gating analysis.

    Args:
        afrr_offers: {h: {up_mw, dn_mw, cap_price_up_eur_mw, cap_price_dn_eur_mw}}
                     (from ReserveOffer dataclass fields, converted to dict)
        mfrr_offers: same structure as afrr_offers
        pv_schedule: {h: {available_mw}} — used to count PV-gated hours
        bess_power_mw: BESS rated power (MW) — for gating threshold check

    Returns dict with per-product offer stats and BESS gating summary.
    """
    result: dict = {}

    for name, offers in [("aFRR", afrr_offers), ("mFRR", mfrr_offers)]:
        if not offers:
            result[name] = {"status": "not_available"}
            continue
        H = sorted(offers.keys())
        up_vals = [offers[h]["up_mw"] for h in H]
        dn_vals = [offers[h]["dn_mw"] for h in H]
        hours_offered_up = sum(1 for v in up_vals if v > 0.01)
        hours_offered_dn = sum(1 for v in dn_vals if v > 0.01)
        result[name] = {
            "hours_offering_up":   hours_offered_up,
            "hours_offering_dn":   hours_offered_dn,
            "avg_up_mw":           round(sum(up_vals) / len(H), 3),
            "avg_dn_mw":           round(sum(dn_vals) / len(H), 3),
            "max_up_mw":           round(max(up_vals), 3),
            "max_dn_mw":           round(max(dn_vals), 3),
            "total_up_mwh":        round(sum(up_vals), 3),
            "total_dn_mwh":        round(sum(dn_vals), 3),
        }
        if "cap_price_up_eur_mw" in offers[H[0]]:
            px_up = [offers[h]["cap_price_up_eur_mw"] for h in H if offers[h]["up_mw"] > 0.01]
            px_dn = [offers[h]["cap_price_dn_eur_mw"] for h in H if offers[h]["dn_mw"] > 0.01]
            result[name]["avg_cap_price_up_eur_mw"] = round(sum(px_up) / len(px_up), 2) if px_up else 0.0
            result[name]["avg_cap_price_dn_eur_mw"] = round(sum(px_dn) / len(px_dn), 2) if px_dn else 0.0

    # BESS PV-gating: hours where BESS upward FRR contribution was zero due to no PV
    if pv_schedule:
        H = sorted(pv_schedule.keys())
        pv_gated_hours = sum(1 for h in H if pv_schedule[h]["available_mw"] < 0.01)
        pv_active_hours = len(H) - pv_gated_hours
        result["bess_pv_gating"] = {
            "hours_pv_available":     pv_active_hours,
            "hours_pv_unavailable":   pv_gated_hours,
            "bess_up_frr_blocked_hours": pv_gated_hours,
            "pv_gating_pct":          round(100.0 * pv_gated_hours / len(H), 1),
        }

    return result
