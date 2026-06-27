"""
summary_kpi_builder.py — compute all KPI sections for the Summary_KPIs sheet.

Returns list of (Section, Metric, Value, Unit) tuples with blank rows between
sections.  Draws from Dispatch_Hourly DataFrame produced by dispatch_sheet_builder
plus ComponentStore solver_metrics.

10 KPI sections:
  1  Economic Overview
  2  PSP Operations
  3  PV Generation
  4  BESS Storage
  5  Reservoir & Hydraulics
  6  aFRR Strategy
  7  mFRR Strategy
  8  Real-Time Delivery
  9  Temporal Patterns
  10 Constraint Verification
"""
from __future__ import annotations

from typing import List, Tuple

import pandas as pd

from common_layer.database import ComponentStore

Row = Tuple[str, str, object, str]   # (Section, Metric, Value, Unit)
_SEP: Row = ("", "", "", "")         # blank separator row between sections


def _r(v, n=2):
    """Round float; return as-is for non-numeric. NaN → 0.0 (openpyxl cannot write NaN)."""
    try:
        f = float(v)
        import math
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return round(f, n)
    except (TypeError, ValueError):
        return v


def build_summary_kpis(delivery_date: str, df: pd.DataFrame) -> List[Row]:
    """Build full KPI list.  df = 24-row Dispatch_Hourly DataFrame."""
    rows: List[Row] = []

    comp = ComponentStore().load(delivery_date) or {}
    solver = comp.get("solver_metrics", {})

    def S(section: str, metric: str, value, unit: str = ""):
        rows.append((section, metric, value, unit))

    # ── SECTION 1: Economic Overview ─────────────────────────────────────────
    sec = "1. Economic Overview"
    total_rev = df["Rev_hour_total_EUR"].sum()
    S(sec, "Total daily P&L",            _r(total_rev), "EUR")
    S(sec, "  DA energy revenue",         _r(df["Rev_DA_EUR"].sum()), "EUR")
    S(sec, "  IDA incremental revenue",   _r(df["Rev_IDA_EUR"].sum()), "EUR")
    S(sec, "  aFRR capacity revenue",     _r(df["Rev_aFRR_cap_EUR"].sum()), "EUR")
    S(sec, "  aFRR activation revenue",   _r(df["Rev_aFRR_act_EUR"].sum()), "EUR")
    S(sec, "  mFRR capacity revenue",     _r(df["Rev_mFRR_cap_EUR"].sum()), "EUR")
    S(sec, "  mFRR activation revenue",   _r(df["Rev_mFRR_act_EUR"].sum()), "EUR")
    S(sec, "  Imbalance settlement",      _r(df["Rev_imbalance_EUR"].sum()), "EUR")
    S(sec, "Reserve share of P&L",
      _r(100 * (df[["Rev_aFRR_cap_EUR","Rev_aFRR_act_EUR",
                     "Rev_mFRR_cap_EUR","Rev_mFRR_act_EUR"]].sum().sum()) / total_rev
         if total_rev else 0.0, 1), "%")
    S(sec, "MILP objective (Day-Ahead)",  _r(solver.get("objective_eur", 0.0)), "EUR")
    S(sec, "MILP solve time",             _r(solver.get("solve_time_sec", 0.0), 1), "s")
    rows.append(_SEP)

    # ── SECTION 2: PSP Operations ────────────────────────────────────────────
    sec = "2. PSP Operations"
    gen_mwh  = df["PSP_gen_MW"].sum()       # MW × 1h
    pump_mwh = df["PSP_pump_MW"].sum()
    sell_h   = df[df["DA_side"] == "SELL"]
    buy_h    = df[df["DA_side"] == "BUY"]
    avg_sell = (sell_h["DA_price_EUR_MWh"] * sell_h["PSP_gen_MW"]).sum() / sell_h["PSP_gen_MW"].sum() \
               if sell_h["PSP_gen_MW"].sum() > 0 else 0.0
    avg_pump = (buy_h["DA_price_EUR_MWh"] * buy_h["PSP_pump_MW"]).sum() / buy_h["PSP_pump_MW"].sum() \
               if buy_h["PSP_pump_MW"].sum() > 0 else 0.0
    spread   = avg_sell - avg_pump

    S(sec, "Total generation",           _r(gen_mwh), "MWh")
    S(sec, "Total pumping energy",        _r(pump_mwh), "MWh")
    S(sec, "Net energy (gen−pump)",       _r(gen_mwh - pump_mwh), "MWh")
    S(sec, "Hours turbining",             int((df["Units_turbining"] > 0).sum()), "h")
    S(sec, "Hours pumping",               int((df["Units_pumping"] > 0).sum()), "h")
    S(sec, "Avg sell price (DA)",         _r(avg_sell), "EUR/MWh")
    S(sec, "Avg pump price (DA)",         _r(avg_pump), "EUR/MWh")
    S(sec, "Price spread captured",       _r(spread), "EUR/MWh")
    S(sec, "Peak turbine output",         _r(df["PSP_gen_MW"].max()), "MW")
    S(sec, "Peak pump load",              _r(df["PSP_pump_MW"].max()), "MW")
    S(sec, "Turbine CF (ref 518.4 MW PSP)",  _r(df["CF_turbine"].mean(), 3), "—")
    S(sec, "Pump CF (ref 446.4 MW PSP)",     _r(df["CF_pump"].mean(), 3), "—")
    S(sec, "Avg turbine efficiency (ηt)", _r(df[df["Eta_turbine_pw"] > 0]["Eta_turbine_pw"].mean(), 4), "—")
    S(sec, "Avg pump efficiency (ηp)",    _r(df[df["Eta_pump_pw"] > 0]["Eta_pump_pw"].mean(), 4), "—")
    S(sec, "Total water turbined",        _r(df["q_turb_total_m3h"].sum(), 0), "m³")
    S(sec, "Total water pumped",          _r(df["q_pump_total_m3h"].sum(), 0), "m³")
    rows.append(_SEP)

    # ── SECTION 3: PV Generation ─────────────────────────────────────────────
    sec = "3. PV Generation"
    pv_avail = df["PV_available_MW"].sum()
    pv_used  = df["PV_used_MW"].sum()
    S(sec, "PV available (forecast)",    _r(pv_avail), "MWh")
    S(sec, "PV used in DA schedule",     _r(pv_used), "MWh")
    S(sec, "PV to BESS",                 _r(df["PV_to_BESS_MW"].sum()), "MWh")
    S(sec, "PV curtailed",               _r(df["PV_curtailed_MW"].sum()), "MWh")
    S(sec, "PV utilisation rate",
      _r(100 * pv_used / pv_avail if pv_avail > 0 else 0.0, 1), "%")
    rows.append(_SEP)

    # ── SECTION 4: BESS Storage ───────────────────────────────────────────────
    sec = "4. BESS Storage"
    S(sec, "Total BESS charge",          _r(df["BESS_total_charge_MW"].sum()), "MWh")
    S(sec, "Total BESS discharge",       _r(df["BESS_discharge_MW"].sum()), "MWh")
    S(sec, "Net BESS throughput",        _r(df["BESS_discharge_MW"].sum() - df["BESS_total_charge_MW"].sum()), "MWh")
    S(sec, "SOC start (h=1)",            _r(df.iloc[0]["BESS_SOC_MWh"]), "MWh")
    S(sec, "SOC end (h=24)",             _r(df.iloc[-1]["BESS_SOC_MWh"]), "MWh")
    S(sec, "SOC min",                    _r(df["BESS_SOC_MWh"].min()), "MWh")
    S(sec, "SOC max",                    _r(df["BESS_SOC_MWh"].max()), "MWh")
    rows.append(_SEP)

    # ── SECTION 5: Reservoir & Hydraulics ────────────────────────────────────
    sec = "5. Reservoir & Hydraulics"
    upper_init = float(comp.get("initial_state", {}).get("upper_reservoir_hm3", 2490.0))
    lower_init = float(comp.get("initial_state", {}).get("lower_reservoir_hm3", 27.0))
    S(sec, "Upper reservoir initial",    _r(upper_init, 4), "hm³")
    S(sec, "Upper reservoir h=1",        _r(df.iloc[0]["Reservoir_upper_hm3"], 4), "hm³")
    S(sec, "Upper reservoir h=24",       _r(df.iloc[-1]["Reservoir_upper_hm3"], 4), "hm³")
    S(sec, "Net upper change (full day)", _r(df.iloc[-1]["Reservoir_upper_hm3"] - upper_init, 4), "hm³")
    S(sec, "Lower reservoir initial",    _r(lower_init, 4), "hm³")
    S(sec, "Lower reservoir h=1",        _r(df.iloc[0]["Reservoir_lower_hm3"], 4), "hm³")
    S(sec, "Lower reservoir h=24",       _r(df.iloc[-1]["Reservoir_lower_hm3"], 4), "hm³")
    S(sec, "Total natural inflow",       _r(df["Reservoir_inflow_m3h"].sum(), 0), "m³")
    S(sec, "Total spill",                _r(df["Spill_m3h"].sum(), 0), "m³")
    S(sec, "Net head (avg)",             _r(df[df["Head_net_m"] > 0]["Head_net_m"].mean(), 1), "m")
    S(sec, "Max mass balance error",     _r(df["Mass_balance_error_hm3"].max(), 6), "hm³")
    rows.append(_SEP)

    # ── SECTION 6: aFRR Strategy ─────────────────────────────────────────────
    sec = "6. aFRR Strategy"
    afrr_up_hrs = int((df["aFRR_up_MW"] > 0).sum())
    afrr_dn_hrs = int((df["aFRR_dn_MW"] > 0).sum())
    S(sec, "Hours offering aFRR up",     afrr_up_hrs, "h")
    S(sec, "Hours offering aFRR dn",     afrr_dn_hrs, "h")
    S(sec, "Avg aFRR up capacity",       _r(df.loc[df["aFRR_up_MW"] > 0, "aFRR_up_MW"].mean()), "MW")
    S(sec, "Avg aFRR dn capacity",       _r(df.loc[df["aFRR_dn_MW"] > 0, "aFRR_dn_MW"].mean()), "MW")
    S(sec, "Total aFRR up offered",      _r(df["aFRR_up_MW"].sum()), "MW·h")
    S(sec, "Total aFRR dn offered",      _r(df["aFRR_dn_MW"].sum()), "MW·h")
    S(sec, "Avg aFRR up cap price",
      _r(df[df["aFRR_up_MW"] > 0]["aFRR_capUp_EUR_MW"].mean()), "EUR/MW")
    S(sec, "Avg aFRR dn cap price",
      _r(df[df["aFRR_dn_MW"] > 0]["aFRR_capDn_EUR_MW"].mean()), "EUR/MW")
    S(sec, "aFRR capacity revenue",      _r(df["Rev_aFRR_cap_EUR"].sum()), "EUR")
    S(sec, "aFRR activation revenue",    _r(df["Rev_aFRR_act_EUR"].sum()), "EUR")
    rows.append(_SEP)

    # ── SECTION 7: mFRR Strategy ─────────────────────────────────────────────
    sec = "7. mFRR Strategy"
    mfrr_up_hrs = int((df["mFRR_up_MW"] > 0).sum())
    mfrr_dn_hrs = int((df["mFRR_dn_MW"] > 0).sum())
    S(sec, "Hours offering mFRR up",     mfrr_up_hrs, "h")
    S(sec, "Hours offering mFRR dn",     mfrr_dn_hrs, "h")
    S(sec, "Avg mFRR up capacity",       _r(df.loc[df["mFRR_up_MW"] > 0, "mFRR_up_MW"].mean()), "MW")
    S(sec, "Avg mFRR dn capacity",       _r(df.loc[df["mFRR_dn_MW"] > 0, "mFRR_dn_MW"].mean()), "MW")
    S(sec, "Total mFRR up offered",      _r(df["mFRR_up_MW"].sum()), "MW·h")
    S(sec, "Total mFRR dn offered",      _r(df["mFRR_dn_MW"].sum()), "MW·h")
    S(sec, "Avg mFRR up cap price",
      _r(df[df["mFRR_up_MW"] > 0]["mFRR_capUp_EUR_MW"].mean()), "EUR/MW")
    S(sec, "Avg mFRR dn cap price",
      _r(df[df["mFRR_dn_MW"] > 0]["mFRR_capDn_EUR_MW"].mean()), "EUR/MW")
    S(sec, "mFRR capacity revenue",      _r(df["Rev_mFRR_cap_EUR"].sum()), "EUR")
    S(sec, "mFRR activation revenue",    _r(df["Rev_mFRR_act_EUR"].sum()), "EUR")
    rows.append(_SEP)

    # ── SECTION 8: Real-Time Delivery ────────────────────────────────────────
    sec = "8. Real-Time Delivery"
    S(sec, "Imbalance settlement",       _r(df["Rev_imbalance_EUR"].sum()), "EUR")
    S(sec, "IDA incremental revenue",    _r(df["Rev_IDA_EUR"].sum()), "EUR")
    S(sec, "Hours IDA1 adjusted",        int((df["IDA1_delta_MW"].abs() > 0.01).sum()), "h")
    S(sec, "Hours IDA2 adjusted",        int((df["IDA2_delta_MW"].abs() > 0.01).sum()), "h")
    S(sec, "Hours IDA3 adjusted",        int((df["IDA3_delta_MW"].abs() > 0.01).sum()), "h")
    S(sec, "Hours XBID adjusted",        int((df["XBID_delta_MW"].abs() > 0.01).sum()), "h")
    rows.append(_SEP)

    # ── SECTION 9: Temporal Patterns ─────────────────────────────────────────
    sec = "9. Temporal Patterns"
    peak_h = int(df.loc[df["Rev_hour_total_EUR"].idxmax(), "Hour"])
    trough_h = int(df.loc[df["DA_price_EUR_MWh"].idxmin(), "Hour"])
    S(sec, "Highest revenue hour",       peak_h, "h")
    S(sec, "Lowest DA price hour",       trough_h, "h")
    S(sec, "DA price min",               _r(df["DA_price_EUR_MWh"].min()), "EUR/MWh")
    S(sec, "DA price max",               _r(df["DA_price_EUR_MWh"].max()), "EUR/MWh")
    S(sec, "DA price avg",               _r(df["DA_price_EUR_MWh"].mean()), "EUR/MWh")
    rows.append(_SEP)

    # ── SECTION 10: Constraint Verification ──────────────────────────────────
    sec = "10. Constraint Verification"
    max_eb = df["Energy_balance_check_MW"].abs().max()
    max_mb = df["Mass_balance_error_hm3"].max()
    gen_hr_min = df["Gen_headroom_MW"].min()
    pmp_hr_min = df["Pump_headroom_MW"].min()
    S(sec, "Max energy balance error",   _r(max_eb, 4),  "MW")
    S(sec, "Max mass balance error",     _r(max_mb, 6),  "hm³")
    S(sec, "Min gen headroom",           _r(gen_hr_min), "MW")
    S(sec, "Min pump headroom",          _r(pmp_hr_min), "MW")
    S(sec, "Energy balance OK?",         "YES" if max_eb < 0.1 else "NO !!!", "")
    S(sec, "Mass balance OK?",           "YES" if max_mb < 1e-4 else "NO !!!", "")
    S(sec, "Gen headroom OK?",           "YES" if gen_hr_min >= -0.01 else "NO !!!", "")
    S(sec, "Pump headroom OK?",          "YES" if pmp_hr_min >= -0.01 else "NO !!!", "")

    return rows
