"""
dispatch_sheet_builder.py — builds the Dispatch_Hourly DataFrame (24 rows).

Reads from:
  ComponentStore  — per-unit PSP, BESS, PV, reservoir, efficiency, inflow,
                    initial_state (reservoir/BESS at gate-open)
  PositionStore   — DA / IDA1 / IDA2 / IDA3 / XBID committed positions + prices
  ReserveStore    — aFRR / mFRR hourly offers (up/dn MW + cap prices)
  ActivationStore — aFRR / mFRR per-ISP activations (grouped to hourly revenue)
  DeliveryStore   — per-ISP scheduled vs actual (grouped to hourly imbalance)

Returns a pandas DataFrame with columns grouped A–L as per the design spec.
Missing component data (if ComponentStore file absent) falls back to zeros.

PHYSICS INTEGRITY NOTES (verified against plant.yaml and MILP constraints):
  - Energy balance: p_net = PSP_net + pv_used + p_dis − p_chg  (p_chg = grid charge
    only; pv_to_bess is internal PV→BESS and does NOT cross the grid boundary)
  - Mass balance: ΔV_upper = (inflow + q_pump − q_turb − spill) × dt / M3_PER_HM3
  - BESS SOC%: referenced to plant capacity 2.0 MWh (plant.yaml: bess.capacity_mwh)
  - Reservoir fill%: referenced to operational bounds from plant.yaml
      upper: 830 hm³ (floor) → 3150 hm³ (usable ceiling)
      lower: 5 hm³ (floor)  → 54 hm³ (capacity)
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from common_layer.database import (
    PositionStore, ReserveStore, DeliveryStore, ActivationStore, ComponentStore,
)

# ── Alqueva physical limits — confirmed from plant.yaml / market.yaml ────────
# Total plant generation envelope = 4×129.6 PSP + 5.0 PV + 1.0 BESS discharge
#   PSP only: 4×129.6 = 518.4 MW; with PV+BESS: 518.4+5+1 = 524.4 MW (bid_limits.max_generation_mw)
# Total plant pump envelope = 4×111.6 PSP pump + 1.0 BESS charge
#   PSP only: 4×111.6 = 446.4 MW; with BESS: 446.4+1 = 447.4 MW (bid_limits.max_pump_mw)
_P_MAX_GEN_MW  = 524.4      # total plant: 4×129.6 PSP + 5 PV + 1 BESS discharge (MW)
_P_MAX_PUMP_MW = 447.4      # total plant: 4×111.6 PSP pump + 1 BESS charge (MW)
_PSP_MAX_GEN_MW  = 518.4    # PSP turbine only: 4×129.6 (for CF_turbine denominator)
_PSP_MAX_PUMP_MW = 446.4    # PSP pump only: 4×111.6 (for CF_pump denominator)
_BESS_CAP_MWH  = 2.0        # BESS capacity (plant.yaml: bess.capacity_mwh)
_M3_PER_HM3    = 1_000_000.0

# Reservoir operational bounds (plant.yaml)
_UPPER_MIN_HM3  = 830.0     # hard operational floor
_UPPER_MAX_HM3  = 3150.0    # usable ceiling (upper_usable_hm3)
_LOWER_MIN_HM3  = 5.0       # hard operational floor
_LOWER_MAX_HM3  = 54.0      # capacity (lower_capacity_hm3)

# Default initial state (plant.yaml initial_state) — used when ComponentStore
# does not yet have initial_state (old runs before this field was added)
_DEFAULT_UPPER_INIT_HM3 = 2490.0
_DEFAULT_LOWER_INIT_HM3 = 27.0


def build_dispatch_hourly(delivery_date: str) -> pd.DataFrame:
    hours = list(range(1, 25))

    # ── Load all stores ────────────────────────────────────────────────────────
    pos  = PositionStore()
    rsvr = ReserveStore()
    comp = ComponentStore().load(delivery_date) or {}

    da_pos   = pos.load_position(delivery_date, "DA")
    ida1_pos = pos.load_position(delivery_date, "IDA1")
    ida2_pos = pos.load_position(delivery_date, "IDA2")
    ida3_pos = pos.load_position(delivery_date, "IDA3")
    xbid_pos = pos.load_position(delivery_date, "XBID")

    # Load cumulative committed position (DA + all IDA/XBID deltas) once — used per hour below
    committed_final: Dict[int, float] = pos.committed_position(delivery_date)

    afrr_off = rsvr.load_reserve(delivery_date, "aFRR")
    mfrr_off = rsvr.load_reserve(delivery_date, "mFRR")

    act_afrr = ActivationStore().load(delivery_date, "aFRR")
    act_mfrr = ActivationStore().load(delivery_date, "mFRR")
    rt_rows  = DeliveryStore().load(delivery_date)

    psp_sched  = comp.get("psp_schedule", {})
    bess_sched = comp.get("bess_schedule", {})
    pv_sched   = comp.get("pv_schedule", {})
    res_traj   = comp.get("reservoir_trajectory", {})
    eff_ph     = comp.get("efficiency_per_hour", {})
    inflow_m3h = comp.get("inflow_m3h", {})

    # Seed reservoir tracker from stored gate-open state, not from the h=1 trajectory value
    init_st = comp.get("initial_state", {})
    prev_upper_hm3 = float(init_st.get("upper_reservoir_hm3", _DEFAULT_UPPER_INIT_HM3))

    # ── Aggregate ISP activation revenue per hour ──────────────────────────────
    # Ramp-corrected ISP duration: eff_isp_h = (isp_min − fat_min/2) / 60
    #   aFRR FAT=5 min:    (15 − 2.5)  / 60 = 0.208333h  (face 0.25h overstates energy by 20%)
    #   mFRR FAT=12.5 min: (15 − 6.25) / 60 = 0.145833h  (face 0.25h overstates energy by 71%)
    # ActivationStore always carries the correct value; these defaults guard against rows
    # written before the effective_isp_h column was added to the schema.
    _EFF_ISP_H_AFRR = round((15 - 5.0 / 2) / 60, 6)    # 0.208333h
    _EFF_ISP_H_MFRR = round((15 - 12.5 / 2) / 60, 6)   # 0.145833h

    afrr_act_rev: Dict[int, float] = {h: 0.0 for h in hours}
    for row in act_afrr:
        h = row["hour"]
        eff_h = row.get("eff_isp_h", _EFF_ISP_H_AFRR)   # ramp-corrected: (15-FAT/2)/60
        rev = (row.get("up_mw", 0.0) * row.get("up_price_eur_mwh", 0.0)
             + row.get("dn_mw", 0.0) * row.get("dn_price_eur_mwh", 0.0)) * eff_h
        afrr_act_rev[h] = afrr_act_rev.get(h, 0.0) + rev

    mfrr_act_rev: Dict[int, float] = {h: 0.0 for h in hours}
    for row in act_mfrr:
        h = row["hour"]
        eff_h = row.get("eff_isp_h", _EFF_ISP_H_MFRR)   # ramp-corrected: (15-12.5/2)/60
        rev = (row.get("up_mw", 0.0) * row.get("up_price_eur_mwh", 0.0)
             + row.get("dn_mw", 0.0) * row.get("dn_price_eur_mwh", 0.0)) * eff_h
        mfrr_act_rev[h] = mfrr_act_rev.get(h, 0.0) + rev

    # ── Aggregate imbalance settlement per hour ────────────────────────────────
    # MIBEL dual-pricing (market.yaml: fallback_long_factor=0.85, fallback_short_factor=1.20)
    # Long (over-delivered): TSO accepts surplus but pays DA × 0.85 (discount)
    # Short (under-delivered): plant buys back shortfall at DA × 1.20 (premium)
    # ISP duration 15 min → energy per ISP = MW × 0.25 h
    _IMB_LONG_FACTOR  = 0.85   # matches market.yaml imbalance.fallback_long_factor
    _IMB_SHORT_FACTOR = 1.20   # matches market.yaml imbalance.fallback_short_factor
    da_price_h = {h: da_pos.get(h, {}).get("price_eur_mwh", 0.0) for h in hours}
    imb_rev: Dict[int, float] = {h: 0.0 for h in hours}
    for row in rt_rows:
        h = row["hour"]
        dev = row.get("actual_mw", 0.0) - row.get("scheduled_mw", 0.0)
        da_p = da_price_h.get(h, 0.0)
        if dev > 0:   # long: over-delivered → receives DA × 0.85 per surplus MWh
            imb_rev[h] = imb_rev.get(h, 0.0) + dev * da_p * _IMB_LONG_FACTOR * 0.25
        elif dev < 0: # short: under-delivered → pays DA × 1.20 per missing MWh (dev is negative)
            imb_rev[h] = imb_rev.get(h, 0.0) + dev * da_p * _IMB_SHORT_FACTOR * 0.25

    # ── Build one row per hour ─────────────────────────────────────────────────
    rows = []
    cum_rev = 0.0
    cum_net_mwh = 0.0

    for h in hours:
        # --- GROUP A: Inputs ---
        da_price = da_pos.get(h, {}).get("price_eur_mwh", 0.0)
        pv_avail = pv_sched.get(h, {}).get("available_mw", 0.0)
        inflow   = float(inflow_m3h.get(h, 0.0))

        # --- GROUP B: PSP plant totals ---
        ps       = psp_sched.get(h, {})
        psp_gen  = ps.get("turbine_mw", 0.0)
        psp_pump = ps.get("pump_mw", 0.0)
        da_vol       = da_pos.get(h, {}).get("volume_mwh", 0.0)  # signed MWh (dt=1h)
        psp_net_da   = da_vol   # MW equivalent (dt=1h so numerically equal)
        final_vol    = committed_final.get(h, psp_net_da)  # cumulative position after all IDA/XBID
        units_turb   = int(sum(ps.get("units_on_turb", [])))
        units_pump_n = int(sum(ps.get("units_on_pump", [])))
        da_side = "SELL" if psp_net_da > 0.01 else ("BUY" if psp_net_da < -0.01 else "IDLE")

        # --- GROUP C: Per-unit PSP (4 units) ---
        u_gen  = ps.get("units_turbine", [0.0] * 4)
        u_pump = ps.get("units_pump",    [0.0] * 4)
        u_on_t = ps.get("units_on_turb", [0]   * 4)
        u_on_p = ps.get("units_on_pump", [0]   * 4)
        u_qt   = ps.get("units_q_turb",  [0.0] * 4)
        u_qp   = ps.get("units_q_pump",  [0.0] * 4)
        q_turb_total = ps.get("q_turb_total_m3h", 0.0)
        q_pump_total = ps.get("q_pump_total_m3h", 0.0)

        # --- GROUP D: PV ---
        pv          = pv_sched.get(h, {})
        pv_used     = pv.get("used_mw", 0.0)
        pv_to_bess  = pv.get("to_bess_mw", 0.0)
        pv_curt     = pv.get("curtailed_mw", 0.0)

        # --- GROUP E: BESS ---
        bs           = bess_sched.get(h, {})
        bess_chg     = bs.get("charge_mw", 0.0)       # grid → BESS (p_chg in MILP)
        bess_tot_chg = bs.get("total_charge_mw", 0.0) # p_chg + pv_to_bess
        bess_dis     = bs.get("discharge_mw", 0.0)
        bess_soc     = bs.get("soc_mwh", 0.0)
        bess_soc_pct = round(100.0 * bess_soc / _BESS_CAP_MWH, 1)  # ref: 2.0 MWh capacity

        # --- GROUP F: Reservoir & hydraulics ---
        rt        = res_traj.get(h, {})
        upper_hm3 = rt.get("upper_hm3", 0.0)
        lower_hm3 = rt.get("lower_hm3", 0.0)
        spill_m3h = rt.get("spill_m3h", 0.0)
        head_m    = rt.get("head_m", 0.0)

        upper_rng = _UPPER_MAX_HM3 - _UPPER_MIN_HM3    # 3150 − 830 = 2320 hm³ usable range
        lower_rng = _LOWER_MAX_HM3 - _LOWER_MIN_HM3    # 54 − 5 = 49 hm³ usable range
        upper_pct = round(100.0 * (upper_hm3 - _UPPER_MIN_HM3) / upper_rng, 1) if upper_rng else 0.0
        lower_pct = round(100.0 * (lower_hm3 - _LOWER_MIN_HM3) / lower_rng, 1) if lower_rng else 0.0

        dV_actual      = upper_hm3 - prev_upper_hm3   # Δ volume vs prior-hour end state
        # Mass balance theoretical: ΔV = (inflow + q_pump − q_turb − spill) × 1h / M3_PER_HM3
        dV_theoretical = (inflow + q_pump_total - q_turb_total - spill_m3h) / _M3_PER_HM3
        mass_balance_err = round(abs(dV_actual - dV_theoretical), 6)
        prev_upper_hm3   = upper_hm3

        # --- GROUP G: Efficiency & capacity factors ---
        ep      = eff_ph.get(h, {})
        eta_trb = round(ep.get("eta_trb_pw", 0.0), 4)
        eta_pmp = round(ep.get("eta_pmp_pw", 0.0), 4)
        # CF denominator is PSP-only (518.4/446.4 MW) — mixing in PV+BESS capacity would understate CF
        cf_trb  = round(psp_gen  / _PSP_MAX_GEN_MW,  4) if _PSP_MAX_GEN_MW  else 0.0
        cf_pmp  = round(psp_pump / _PSP_MAX_PUMP_MW, 4) if _PSP_MAX_PUMP_MW else 0.0

        # --- GROUP H: IDA re-optimisation ---
        ida1_vol = ida1_pos.get(h, {}).get("volume_mwh", psp_net_da)
        ida1_prc = ida1_pos.get(h, {}).get("price_eur_mwh", da_price)
        ida1_del = round(ida1_vol - psp_net_da, 4)
        ida1_spr = round(ida1_prc - da_price, 4)

        ida2_vol = ida2_pos.get(h, {}).get("volume_mwh", ida1_vol)
        ida2_prc = ida2_pos.get(h, {}).get("price_eur_mwh", ida1_prc)
        ida2_del = round(ida2_vol - ida1_vol, 4)
        # Zero spread for hours outside IDA2 delivery window (H1–H2 frozen; market.yaml delivery_hours [3,24])
        # Propagated fallback price would otherwise misrepresent a trade that never happened.
        ida2_spr = round(ida2_prc - da_price, 4) if h in ida2_pos else 0.0

        ida3_vol = ida3_pos.get(h, {}).get("volume_mwh", ida2_vol)
        ida3_prc = ida3_pos.get(h, {}).get("price_eur_mwh", ida2_prc)
        ida3_del = round(ida3_vol - ida2_vol, 4)
        # Zero spread for hours outside IDA3 delivery window (H1–H11 frozen; market.yaml delivery_hours [12,24])
        ida3_spr = round(ida3_prc - da_price, 4) if h in ida3_pos else 0.0

        xbid_vol = xbid_pos.get(h, {}).get("volume_mwh", ida3_vol)
        xbid_del = round(xbid_vol - ida3_vol, 4)
        ida_cum  = round(final_vol - psp_net_da, 4)

        # --- GROUP I: aFRR ---
        af       = afrr_off.get(h, {})
        afrr_up  = af.get("up_mw", 0.0)
        afrr_dn  = af.get("dn_mw", 0.0)
        afrr_cup = af.get("cap_up_eur_mw", 0.0)
        afrr_cdn = af.get("cap_dn_eur_mw", 0.0)

        # --- GROUP J: mFRR ---
        mf       = mfrr_off.get(h, {})
        mfrr_up  = mf.get("up_mw", 0.0)
        mfrr_dn  = mf.get("dn_mw", 0.0)
        mfrr_cup = mf.get("cap_up_eur_mw", 0.0)
        mfrr_cdn = mf.get("cap_dn_eur_mw", 0.0)

        # --- GROUP K: Physical headroom checks ---
        # PR-11: use committed net position (final_vol) — matches reserve_offer_builder.py.
        # gen_headroom  = p_gen_cap  - final_vol  - afrr_up - mfrr_up
        # pump_headroom = final_vol  + p_pump_cap - afrr_dn - mfrr_dn
        # In pump mode (final_vol=-300): gen_hr = 524.4-(-300) = 824.4 MW (full ramp-up range)
        # In gen mode  (final_vol=+500): gen_hr = 524.4-500     =  24.4 MW (near cap)
        gen_hr  = round(_P_MAX_GEN_MW  - final_vol - afrr_up - mfrr_up, 2)
        pump_hr = round(final_vol + _P_MAX_PUMP_MW - afrr_dn - mfrr_dn, 2)

        # --- GROUP L: Energy balance verification ---
        # MILP INV-1: p_net = (PSP_gen − PSP_pump) + pv_used + p_dis − p_chg
        # pv_to_bess is internal (PV panels → BESS internal bus), not grid-crossing,
        # so bess_chg (grid charge only) enters the balance — not bess_tot_chg.
        net_components = psp_gen - psp_pump + pv_used + bess_dis - bess_chg
        energy_balance = round(psp_net_da - net_components, 4)

        # --- GROUP L: Revenue per hour ---
        # DA settlement: committed DA volume × DA clearing price
        rev_da          = round(da_price * psp_net_da, 2)
        # IDA incremental: each IDA gate settles its delta volume at its clearing price
        xbid_prc        = xbid_pos.get(h, {}).get("price_eur_mwh", ida3_prc)
        rev_ida         = round(ida1_prc * ida1_del + ida2_prc * ida2_del
                                + ida3_prc * ida3_del + xbid_prc * xbid_del, 2)
        # aFRR capacity revenue: MW × EUR/MW/h × 1h
        rev_afrr_cap_up = round(afrr_up * afrr_cup, 2)
        rev_afrr_cap_dn = round(afrr_dn * afrr_cdn, 2)
        rev_afrr_cap    = round(rev_afrr_cap_up + rev_afrr_cap_dn, 2)
        rev_afrr_act    = round(afrr_act_rev.get(h, 0.0), 2)
        # mFRR capacity revenue: MW × EUR/MW/h × 1h
        rev_mfrr_cap_up = round(mfrr_up * mfrr_cup, 2)
        rev_mfrr_cap_dn = round(mfrr_dn * mfrr_cdn, 2)
        rev_mfrr_cap    = round(rev_mfrr_cap_up + rev_mfrr_cap_dn, 2)
        rev_mfrr_act    = round(mfrr_act_rev.get(h, 0.0), 2)
        rev_imbalance   = round(imb_rev.get(h, 0.0), 2)
        rev_total       = round(rev_da + rev_ida + rev_afrr_cap + rev_afrr_act
                                + rev_mfrr_cap + rev_mfrr_act + rev_imbalance, 2)
        cum_rev        += rev_total
        cum_net_mwh    += final_vol

        rows.append({
            # A — Inputs
            "Hour":                       h,
            "DA_price_EUR_MWh":           round(da_price, 4),
            "PV_available_MW":            round(pv_avail, 4),
            "Reservoir_inflow_m3h":       round(inflow, 2),
            # B — PSP plant totals
            "DA_side":                    da_side,
            "PSP_gen_MW":                 round(psp_gen, 4),
            "PSP_pump_MW":                round(psp_pump, 4),
            # Plant_net includes PSP + PV + BESS (e.g. 200+4.2+0.5 = 204.7 MW); distinct from PSP_gen_MW
            "Plant_net_DA_MW":            round(psp_net_da, 4),
            "Plant_net_final_MW":         round(final_vol, 4),
            "Units_turbining":            units_turb,
            "Units_pumping":              units_pump_n,
            # C — Per-unit PSP
            "PSP_gen_u1_MW":              round(u_gen[0]  if len(u_gen)  > 0 else 0.0, 4),
            "PSP_gen_u2_MW":              round(u_gen[1]  if len(u_gen)  > 1 else 0.0, 4),
            "PSP_gen_u3_MW":              round(u_gen[2]  if len(u_gen)  > 2 else 0.0, 4),
            "PSP_gen_u4_MW":              round(u_gen[3]  if len(u_gen)  > 3 else 0.0, 4),
            "PSP_pump_u1_MW":             round(u_pump[0] if len(u_pump) > 0 else 0.0, 4),
            "PSP_pump_u2_MW":             round(u_pump[1] if len(u_pump) > 1 else 0.0, 4),
            "PSP_pump_u3_MW":             round(u_pump[2] if len(u_pump) > 2 else 0.0, 4),
            "PSP_pump_u4_MW":             round(u_pump[3] if len(u_pump) > 3 else 0.0, 4),
            "On_turb_u1":                 int(u_on_t[0]  if len(u_on_t) > 0 else 0),
            "On_turb_u2":                 int(u_on_t[1]  if len(u_on_t) > 1 else 0),
            "On_turb_u3":                 int(u_on_t[2]  if len(u_on_t) > 2 else 0),
            "On_turb_u4":                 int(u_on_t[3]  if len(u_on_t) > 3 else 0),
            "On_pump_u1":                 int(u_on_p[0]  if len(u_on_p) > 0 else 0),
            "On_pump_u2":                 int(u_on_p[1]  if len(u_on_p) > 1 else 0),
            "On_pump_u3":                 int(u_on_p[2]  if len(u_on_p) > 2 else 0),
            "On_pump_u4":                 int(u_on_p[3]  if len(u_on_p) > 3 else 0),
            "q_turb_u1_m3h":              round(u_qt[0]  if len(u_qt) > 0 else 0.0, 2),
            "q_turb_u2_m3h":              round(u_qt[1]  if len(u_qt) > 1 else 0.0, 2),
            "q_turb_u3_m3h":             round(u_qt[2]  if len(u_qt) > 2 else 0.0, 2),
            "q_turb_u4_m3h":              round(u_qt[3]  if len(u_qt) > 3 else 0.0, 2),
            "q_pump_u1_m3h":              round(u_qp[0]  if len(u_qp) > 0 else 0.0, 2),
            "q_pump_u2_m3h":              round(u_qp[1]  if len(u_qp) > 1 else 0.0, 2),
            "q_pump_u3_m3h":              round(u_qp[2]  if len(u_qp) > 2 else 0.0, 2),
            "q_pump_u4_m3h":              round(u_qp[3]  if len(u_qp) > 3 else 0.0, 2),
            "q_turb_total_m3h":           round(q_turb_total, 2),
            "q_pump_total_m3h":           round(q_pump_total, 2),
            # D — PV
            "PV_used_MW":                 round(pv_used, 4),
            "PV_to_BESS_MW":              round(pv_to_bess, 4),
            "PV_curtailed_MW":            round(pv_curt, 4),
            # E — BESS
            "BESS_charge_MW":             round(bess_chg, 4),
            "BESS_total_charge_MW":       round(bess_tot_chg, 4),
            "BESS_discharge_MW":          round(bess_dis, 4),
            "BESS_SOC_MWh":               round(bess_soc, 4),
            "BESS_SOC_pct":               bess_soc_pct,  # ref to 2.0 MWh capacity
            # F — Reservoir & hydraulics
            "Reservoir_upper_hm3":        round(upper_hm3, 4),
            "Reservoir_lower_hm3":        round(lower_hm3, 4),
            "Reservoir_upper_pct":        upper_pct,   # ref to 830–3150 hm³ range
            "Reservoir_lower_pct":        lower_pct,   # ref to 5–54 hm³ range
            "Head_net_m":                 round(head_m, 2),
            "Spill_m3h":                  round(spill_m3h, 2),
            "dReservoir_upper_hm3":       round(dV_actual, 6),
            "dReservoir_theoretical_hm3": round(dV_theoretical, 6),
            "Mass_balance_error_hm3":     mass_balance_err,
            # G — Efficiency & capacity factors
            "Eta_turbine_pw":             eta_trb,
            "Eta_pump_pw":                eta_pmp,
            "CF_turbine":                 cf_trb,
            "CF_pump":                    cf_pmp,
            # H — IDA re-optimisation
            "IDA1_price_EUR_MWh":         round(ida1_prc, 4),
            "IDA1_spread_EUR_MWh":        ida1_spr,
            "IDA1_delta_MW":              ida1_del,
            "IDA2_price_EUR_MWh":         round(ida2_prc, 4),
            "IDA2_spread_EUR_MWh":        ida2_spr,
            "IDA2_delta_MW":              ida2_del,
            "IDA3_price_EUR_MWh":         round(ida3_prc, 4),
            "IDA3_spread_EUR_MWh":        ida3_spr,
            "IDA3_delta_MW":              ida3_del,
            "XBID_delta_MW":              xbid_del,
            "IDA_cumulative_delta_MW":    ida_cum,
            # I — aFRR
            "aFRR_up_MW":                 round(afrr_up, 4),
            "aFRR_dn_MW":                 round(afrr_dn, 4),
            "aFRR_capUp_EUR_MW":          round(afrr_cup, 4),
            "aFRR_capDn_EUR_MW":          round(afrr_cdn, 4),
            # J — mFRR
            "mFRR_up_MW":                 round(mfrr_up, 4),
            "mFRR_dn_MW":                 round(mfrr_dn, 4),
            "mFRR_capUp_EUR_MW":          round(mfrr_cup, 4),
            "mFRR_capDn_EUR_MW":          round(mfrr_cdn, 4),
            # K — Headroom checks (≥ 0 = physical capacity not exceeded)
            "Gen_headroom_MW":            gen_hr,
            "Pump_headroom_MW":           pump_hr,
            # L — Balance and revenue
            "Energy_balance_check_MW":    energy_balance,   # should be 0
            "Rev_DA_EUR":                 rev_da,
            "Rev_IDA_EUR":                rev_ida,
            "Rev_aFRR_cap_up_EUR":        rev_afrr_cap_up,
            "Rev_aFRR_cap_dn_EUR":        rev_afrr_cap_dn,
            "Rev_aFRR_cap_EUR":           rev_afrr_cap,
            "Rev_aFRR_act_EUR":           rev_afrr_act,
            "Rev_mFRR_cap_up_EUR":        rev_mfrr_cap_up,
            "Rev_mFRR_cap_dn_EUR":        rev_mfrr_cap_dn,
            "Rev_mFRR_cap_EUR":           rev_mfrr_cap,
            "Rev_mFRR_act_EUR":           rev_mfrr_act,
            "Rev_imbalance_EUR":          rev_imbalance,
            "Rev_hour_total_EUR":         rev_total,
            "Cum_Rev_EUR":                round(cum_rev, 2),
            "Cum_Net_MWh":                round(cum_net_mwh, 4),
        })

    df = pd.DataFrame(rows)
    # Zero-out solver floating-point noise near zero
    num_cols = df.select_dtypes(include="number").columns
    df[num_cols] = df[num_cols].mask(df[num_cols].abs() < 1e-6, 0.0)
    # Replace any NaN/Inf that openpyxl cannot write (causes silent Excel failure)
    df[num_cols] = df[num_cols].fillna(0.0).replace([float("inf"), float("-inf")], 0.0)
    return df
