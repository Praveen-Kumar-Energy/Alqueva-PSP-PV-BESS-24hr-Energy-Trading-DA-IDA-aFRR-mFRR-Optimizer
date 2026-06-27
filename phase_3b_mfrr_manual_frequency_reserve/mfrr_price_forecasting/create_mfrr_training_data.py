"""
create_mfrr_training_data.py — generate synthetic mFRR capacity price training data.

REN joined the European mFRR platform MARI on 27 Nov 2024 (REN accession date).
Historical MARI clearing data for Portugal is therefore limited to ~13 months.
Synthetic proxy anchored to European MARI market structure (German/French MARI data
used as reference: cap_up mean ~8-10 EUR/MW, cap_dn mean ~7-8 EUR/MW).

mFRR is the SLOWER manual reserve (FAT 12.5 min vs aFRR 5 min). Its capacity price
is lower than aFRR but is independently driven by MARI supply/demand — NOT a fixed
fraction of aFRR. The two markets can diverge significantly during scarcity events.

Typical MARI mFRR clearing ranges (European proxy, eSIO/MARI reports):
  cap_up : 2 - 40 EUR/MW  (mean ~9)
  cap_dn : 1 - 25 EUR/MW  (mean ~7)
  ceiling: 250 EUR/MW (REN regulatory cap)

Output: mfrr_training_data_2024_2025.xlsx  (sheet MFRR_2024_2025)
Columns: Date, Hour, price_DA_PT_EUR_MWh, cap_up_EUR_MW, cap_dn_EUR_MW
Rows   : ~9,576  (2024-11-27 to 2025-12-31, H1-H24)

Run once:
    python phase_3b_mfrr_manual_frequency_reserve/mfrr_price_forecasting/create_mfrr_training_data.py
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

_HERE    = os.path.dirname(os.path.abspath(__file__))
_OUT     = os.path.join(_HERE, "mfrr_training_data_2024_2025.xlsx")
_SHEET   = "MFRR_2024_2025"
_START   = "2024-11-27"   # REN accession to MARI
_END     = "2025-12-31"
_SEED    = 2024
_HOURS   = list(range(1, 25))
_CAP_MAX = 250.0   # REN regulatory ceiling EUR/MW


def _da_price(hour: int, month: int, dow: int,
              daily_shock: float, rng: np.random.Generator) -> float:
    base  = 65.0
    peak  = 25.0 * np.sin(np.pi * (hour - 6) / 14) if 6 <= hour <= 20 else -5.0
    solar = -15.0 * max(0.0, np.sin(np.pi * (hour - 9) / 8)) if month in (4,5,6,7,8,9) else 0.0
    wkend = -8.0 if dow >= 5 else 0.0
    noise = rng.normal(0, 6.0)
    return round(float(np.clip(base + peak + solar + wkend + daily_shock + noise,
                               -50.0, 300.0)), 2)


def _daily_cap_ou(rng: np.random.Generator, mean: float, sigma: float,
                  theta: float = 0.35) -> np.ndarray:
    """OU cap price process for one day (24 steps), reset each day."""
    daily_bias = rng.normal(mean, mean * 0.30)
    s = np.zeros(24)
    s[0] = daily_bias + rng.normal(0, sigma)
    for i in range(1, 24):
        s[i] = s[i-1] + theta * (daily_bias - s[i-1]) + sigma * rng.normal()
    return np.clip(s, 0.0, _CAP_MAX)


def generate() -> pd.DataFrame:
    rng   = np.random.default_rng(_SEED)
    dates = pd.date_range(_START, _END, freq="D")
    rows  = []

    for d in dates:
        month, dow  = d.month, d.dayofweek
        daily_shock = rng.normal(0, 50.0)
        da_prices   = [_da_price(h, month, dow, daily_shock, rng) for h in _HOURS]

        da_arr = np.array(da_prices)
        da_min, da_max = da_arr.min(), da_arr.max()
        span = max(1.0, da_max - da_min)

        # cap_up mean: higher in winter (heating scarcity), lower at weekends
        seasonal_adj = 3.0 if month in (12, 1, 2) else (-2.0 if month in (6, 7, 8) else 0.0)
        weekend_adj  = -2.0 if dow >= 5 else 0.0
        cap_up_mean  = 9.0 + seasonal_adj + weekend_adj

        cap_up_ou = _daily_cap_ou(rng, mean=cap_up_mean, sigma=3.5)
        cap_dn_ou = _daily_cap_ou(rng, mean=7.0, sigma=2.5)

        for i, h in enumerate(_HOURS):
            da_p     = da_prices[i]
            scarcity = (da_p - da_min) / span   # 0..1

            # upward reserve: more expensive in high-DA (tight supply) hours
            cap_up = cap_up_ou[i] + 8.0 * scarcity
            # downward reserve: more expensive in surplus (low-DA) hours
            cap_dn = cap_dn_ou[i] + 4.0 * (1.0 - scarcity)

            # solar hours: more downward reserve needed (PV surplus)
            if month in (4, 5, 6, 7, 8, 9) and 11 <= h <= 15:
                cap_dn += 2.0

            rows.append({
                "Date"               : d.date(),
                "Hour"               : h,
                "price_DA_PT_EUR_MWh": da_p,
                "cap_up_EUR_MW"      : round(float(np.clip(cap_up, 0.0, _CAP_MAX)), 2),
                "cap_dn_EUR_MW"      : round(float(np.clip(cap_dn, 0.0, _CAP_MAX)), 2),
            })

    return pd.DataFrame(rows)


def main() -> None:
    print(f"Generating mFRR training data {_START} to {_END} ...")
    df = generate()

    df["_date_ts"] = pd.to_datetime(df["Date"])
    corr_up   = df["price_DA_PT_EUR_MWh"].corr(df["cap_up_EUR_MW"])
    corr_dn   = df["price_DA_PT_EUR_MWh"].corr(df["cap_dn_EUR_MW"])
    neg_cap   = ((df["cap_up_EUR_MW"] < 0) | (df["cap_dn_EUR_MW"] < 0)).sum()
    ceil_viol = ((df["cap_up_EUR_MW"] > _CAP_MAX) | (df["cap_dn_EUR_MW"] > _CAP_MAX)).sum()
    solar_dn  = df[df["_date_ts"].dt.month.isin([4,5,6,7,8,9])
                   & df["Hour"].between(11, 15)]["cap_dn_EUR_MW"].mean()
    wday_up   = df[df["_date_ts"].dt.dayofweek < 5]["cap_up_EUR_MW"].mean()
    wend_up   = df[df["_date_ts"].dt.dayofweek >= 5]["cap_up_EUR_MW"].mean()
    winter_up = df[df["_date_ts"].dt.month.isin([12, 1, 2])]["cap_up_EUR_MW"].mean()
    summer_up = df[df["_date_ts"].dt.month.isin([6, 7, 8])]["cap_up_EUR_MW"].mean()
    up_gt_dn  = (df["cap_up_EUR_MW"].mean() > df["cap_dn_EUR_MW"].mean())
    df.drop(columns=["_date_ts"], inplace=True)

    print(f"  Rows              : {len(df):,}")
    print(f"  cap_up mean/std   : {df['cap_up_EUR_MW'].mean():.2f} / {df['cap_up_EUR_MW'].std():.2f} EUR/MW")
    print(f"  cap_dn mean/std   : {df['cap_dn_EUR_MW'].mean():.2f} / {df['cap_dn_EUR_MW'].std():.2f} EUR/MW")
    print(f"  DA-capUp corr     : {corr_up:.4f}  (expect >0) {'OK' if corr_up > 0 else 'FAIL'}")
    print(f"  DA-capDn corr     : {corr_dn:.4f}  (expect <0) {'OK' if corr_dn < 0 else 'FAIL'}")
    print(f"  Negative cap      : {neg_cap}  (expect 0) {'OK' if neg_cap == 0 else 'FAIL'}")
    print(f"  Ceiling violations: {ceil_viol}  (expect 0) {'OK' if ceil_viol == 0 else 'FAIL'}")
    print(f"  cap_up > cap_dn   : {df['cap_up_EUR_MW'].mean():.2f} vs {df['cap_dn_EUR_MW'].mean():.2f}  (expect up>dn) {'OK' if up_gt_dn else 'FAIL'}")
    print(f"  Solar dn H11-15   : {solar_dn:.2f} EUR/MW  (expect > overall mean {df['cap_dn_EUR_MW'].mean():.2f}) {'OK' if solar_dn > df['cap_dn_EUR_MW'].mean() else 'FAIL'}")
    print(f"  Weekday vs weekend: {wday_up:.2f} vs {wend_up:.2f} EUR/MW  (expect weekday > weekend) {'OK' if wday_up > wend_up else 'FAIL'}")
    print(f"  Winter vs summer  : {winter_up:.2f} vs {summer_up:.2f} EUR/MW  (expect winter > summer) {'OK' if winter_up > summer_up else 'FAIL'}")

    with pd.ExcelWriter(_OUT, engine="openpyxl") as w:
        df.to_excel(w, sheet_name=_SHEET, index=False)
    print(f"\nSaved: {_OUT}  ({len(df):,} rows, sheet={_SHEET})")


if __name__ == "__main__":
    main()
