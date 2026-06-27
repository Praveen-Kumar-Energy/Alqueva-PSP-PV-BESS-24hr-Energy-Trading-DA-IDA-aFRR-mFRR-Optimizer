"""
create_afrr_training_data.py — generate synthetic aFRR capacity price training data.

REN publishes aFRR clearing prices on the eSIO transparency portal. Data available
from 2019 (when REN harmonised with ENTSO-E aFRR specifications). We use 2019-2025
(6 years) — much richer than the ~18 months of SIDC IDA data.

Synthetic proxy: OU cap price process anchored to DA energy price scarcity signal.
  cap_up  : upward reserve (TSO activates when frequency drops, supply is tight)
             → higher in high-DA-price hours (peak demand, scarce supply)
  cap_dn  : downward reserve (TSO activates when frequency rises, surplus generation)
             → higher in low-DA-price hours (off-peak, solar surplus)

Typical REN aFRR clearing ranges (eSIO):
  cap_up : 5 - 80 EUR/MW  (mean ~25)
  cap_dn : 3 - 40 EUR/MW  (mean ~12)
  ceiling: 250 EUR/MW (REN regulatory cap, rarely approached)

Output: afrr_training_data_2019_2025.xlsx  (sheet AFRR_2019_2025)
Columns: Date, Hour, price_DA_PT_EUR_MWh, cap_up_EUR_MW, cap_dn_EUR_MW
Rows   : ~61,368  (2019-01-01 to 2025-12-31, H1-H24)

Run once:
    python phase_3a_afrr_automatic_frequency_reserve/afrr_price_forecasting/create_afrr_training_data.py
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

_HERE  = os.path.dirname(os.path.abspath(__file__))
_OUT   = os.path.join(_HERE, "afrr_training_data_2019_2025.xlsx")
_SHEET = "AFRR_2019_2025"
_START = "2019-01-01"
_END   = "2025-12-31"
_SEED  = 2019
_HOURS = list(range(1, 25))
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
                  theta: float = 0.40) -> np.ndarray:
    """OU cap price process for one day (24 steps), reset each day."""
    daily_bias = rng.normal(mean, mean * 0.25)
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

        # cap_up mean: higher in winter (heating demand / scarcity), lower at weekends
        seasonal_adj = 6.0 if month in (12, 1, 2) else (-3.0 if month in (6, 7, 8) else 0.0)
        weekend_adj  = -4.0 if dow >= 5 else 0.0
        cap_up_mean  = 22.0 + seasonal_adj + weekend_adj

        # base OU for each direction; scarcity signal adjusts mean per hour
        cap_up_ou = _daily_cap_ou(rng, mean=cap_up_mean, sigma=5.0)
        cap_dn_ou = _daily_cap_ou(rng, mean=10.0, sigma=3.0)

        for i, h in enumerate(_HOURS):
            da_p     = da_prices[i]
            scarcity = (da_p - da_min) / span   # 0..1; high DA = high scarcity

            # upward reserve: pricier in tight (high DA) hours
            cap_up = cap_up_ou[i] + 20.0 * scarcity
            # downward reserve: pricier in surplus (low DA) hours
            cap_dn = cap_dn_ou[i] + 10.0 * (1.0 - scarcity)

            # solar hours: more downward reserve needed (PV surplus)
            if month in (4, 5, 6, 7, 8, 9) and 11 <= h <= 15:
                cap_dn += 4.0

            rows.append({
                "Date"              : d.date(),
                "Hour"              : h,
                "price_DA_PT_EUR_MWh": da_p,
                "cap_up_EUR_MW"     : round(float(np.clip(cap_up, 0.0, _CAP_MAX)), 2),
                "cap_dn_EUR_MW"     : round(float(np.clip(cap_dn, 0.0, _CAP_MAX)), 2),
            })

    return pd.DataFrame(rows)


def main() -> None:
    print(f"Generating aFRR training data {_START} to {_END} ...")
    df = generate()

    df["_date_ts"] = pd.to_datetime(df["Date"])
    corr_up   = df["price_DA_PT_EUR_MWh"].corr(df["cap_up_EUR_MW"])
    corr_dn   = df["price_DA_PT_EUR_MWh"].corr(df["cap_dn_EUR_MW"])
    neg_cap   = ((df["cap_up_EUR_MW"] < 0) | (df["cap_dn_EUR_MW"] < 0)).sum()
    ceil_viol = ((df["cap_up_EUR_MW"] > _CAP_MAX) | (df["cap_dn_EUR_MW"] > _CAP_MAX)).sum()
    solar_dn  = df[df["_date_ts"].dt.month.isin([4,5,6,7,8,9])
                   & df["Hour"].between(11, 15)]["cap_dn_EUR_MW"].mean()
    peak_up   = df[df["Hour"].between(7, 22)]["cap_up_EUR_MW"].mean()
    offpk_up  = df[(df["Hour"] <= 6) | (df["Hour"] >= 23)]["cap_up_EUR_MW"].mean()
    wday_up   = df[df["_date_ts"].dt.dayofweek < 5]["cap_up_EUR_MW"].mean()
    wend_up   = df[df["_date_ts"].dt.dayofweek >= 5]["cap_up_EUR_MW"].mean()
    winter_up = df[df["_date_ts"].dt.month.isin([12, 1, 2])]["cap_up_EUR_MW"].mean()
    summer_up = df[df["_date_ts"].dt.month.isin([6, 7, 8])]["cap_up_EUR_MW"].mean()
    df.drop(columns=["_date_ts"], inplace=True)

    print(f"  Rows              : {len(df):,}")
    print(f"  cap_up mean/std   : {df['cap_up_EUR_MW'].mean():.2f} / {df['cap_up_EUR_MW'].std():.2f} EUR/MW")
    print(f"  cap_dn mean/std   : {df['cap_dn_EUR_MW'].mean():.2f} / {df['cap_dn_EUR_MW'].std():.2f} EUR/MW")
    print(f"  DA-capUp corr     : {corr_up:.4f}  (expect >0) {'OK' if corr_up > 0 else 'FAIL'}")
    print(f"  DA-capDn corr     : {corr_dn:.4f}  (expect <0) {'OK' if corr_dn < 0 else 'FAIL'}")
    print(f"  Negative cap      : {neg_cap}  (expect 0) {'OK' if neg_cap == 0 else 'FAIL'}")
    print(f"  Ceiling violations: {ceil_viol}  (expect 0) {'OK' if ceil_viol == 0 else 'FAIL'}")
    print(f"  Solar dn H11-15   : {solar_dn:.2f} EUR/MW  (expect > overall mean) {'OK' if solar_dn > df['cap_dn_EUR_MW'].mean() else 'FAIL'}")
    print(f"  Peak capUp        : {peak_up:.2f} EUR/MW  (expect > off-peak {offpk_up:.2f}) {'OK' if peak_up > offpk_up else 'FAIL'}")
    print(f"  Weekday vs weekend: {wday_up:.2f} vs {wend_up:.2f} EUR/MW  (expect weekday > weekend) {'OK' if wday_up > wend_up else 'FAIL'}")
    print(f"  Winter vs summer  : {winter_up:.2f} vs {summer_up:.2f} EUR/MW  (expect winter > summer) {'OK' if winter_up > summer_up else 'FAIL'}")

    with pd.ExcelWriter(_OUT, engine="openpyxl") as w:
        df.to_excel(w, sheet_name=_SHEET, index=False)
    print(f"\nSaved: {_OUT}  ({len(df):,} rows, sheet={_SHEET})")


if __name__ == "__main__":
    main()
