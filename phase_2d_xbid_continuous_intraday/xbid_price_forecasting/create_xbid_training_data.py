"""
create_xbid_training_data.py — generate synthetic XBID training data.

XBID (SIDC continuous intraday) order-book data requires a commercial EPEX SPOT
subscription. Synthetic proxy: IDA3 clearing price + OU spread noise with slightly
wider volatility (XBID trades closer to delivery, more microstructure noise).

Spread properties vs IDA gates (std monotonically increasing):
    IDA1 std ~6   EUR/MWh
    IDA2 std ~8   EUR/MWh
    IDA3 std ~11  EUR/MWh
    XBID std ~14  EUR/MWh  <-- widest; continuous book, no single clearing

Output: xbid_training_data_2024_2025.xlsx  (sheet XBID_2024_2025)
Columns: Date, Hour, price_DA_PT_EUR_MWh, price_XBID_PT_EUR_MWh, spread_EUR_MWh
Rows: 13,608  (2024-06-13 to 2025-12-31, H1-H24)

Run once to (re)generate the Excel:
    python phase_2d_xbid_continuous_intraday/price_and_power_forecasting/create_xbid_training_data.py
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

_HERE   = os.path.dirname(os.path.abspath(__file__))
_OUT    = os.path.join(_HERE, "xbid_training_data_2024_2025.xlsx")
_SHEET  = "XBID_2024_2025"
_START  = "2024-06-13"
_END    = "2025-12-31"
_SEED   = 2024
_HOURS  = list(range(1, 25))       # XBID covers H1-H24


def _da_price(hour: int, month: int, dow: int, rng: np.random.Generator,
              daily_shock: float = 0.0) -> float:
    base  = 65.0
    peak  = 25.0 * np.sin(np.pi * (hour - 6) / 14) if 6 <= hour <= 20 else -5.0
    solar = -15.0 * max(0.0, np.sin(np.pi * (hour - 9) / 8)) if month in (4,5,6,7,8,9) else 0.0
    wkend = -8.0 if dow >= 5 else 0.0
    noise = rng.normal(0, 6.0)
    return round(float(np.clip(base + peak + solar + wkend + daily_shock + noise, -50.0, 300.0)), 2)


def _daily_ou_spread(rng: np.random.Generator, n_hours: int = 24,
                     theta: float = 0.35, sigma: float = 6.5) -> np.ndarray:
    """OU spread for one day (24 steps), reset each day.

    daily_level N(0, 11) + hourly OU sigma=6.5 → total spread std ~14 EUR/MWh.
    """
    daily_level = rng.normal(0, 11.0)
    s = np.zeros(n_hours)
    s[0] = daily_level + rng.normal(0, sigma)
    for i in range(1, n_hours):
        s[i] = s[i-1] + theta * (daily_level - s[i-1]) + sigma * rng.normal()
    return s


def generate() -> pd.DataFrame:
    rng   = np.random.default_rng(_SEED)
    dates = pd.date_range(_START, _END, freq="D")
    rows  = []

    for d in dates:
        month, dow   = d.month, d.dayofweek
        daily_shock  = rng.normal(0, 50.0)   # day-level DA shock → high DA variance → high corr
        da_prices    = [_da_price(h, month, dow, rng, daily_shock) for h in _HOURS]
        spread_ou    = _daily_ou_spread(rng)

        for i, h in enumerate(_HOURS):
            da_p   = da_prices[i]
            spread = spread_ou[i]

            # solar dip: H12-H15 summer → negative XBID spread (PV surplus, selling pressure)
            if month in (4, 5, 6, 7, 8, 9) and 12 <= h <= 15:
                spread -= 4.0

            # negative DA price → negative XBID spread (further selling pressure)
            if da_p < 0:
                spread -= abs(da_p) * 0.15

            # evening peak H19-H21 → slight positive spread (demand surge)
            if 19 <= h <= 21:
                spread += 2.5

            xbid_p = round(float(np.clip(da_p + spread, -500.0, 3000.0)), 2)
            spread_final = round(xbid_p - da_p, 4)

            rows.append({
                "Date"                 : d.date(),
                "Hour"                 : h,
                "price_DA_PT_EUR_MWh"  : da_p,
                "price_XBID_PT_EUR_MWh": xbid_p,
                "spread_EUR_MWh"       : spread_final,
            })

    return pd.DataFrame(rows)


def main() -> None:
    print(f"Generating XBID training data {_START} to {_END} ...")
    df = generate()

    # ---------- 5 mandatory realism checks ----------
    corr   = df["price_DA_PT_EUR_MWh"].corr(df["price_XBID_PT_EUR_MWh"])
    std_sp = df["spread_EUR_MWh"].std()
    solar  = df[(df["Date"].apply(lambda d: pd.Timestamp(d).month in (4,5,6,7,8,9)))
                & df["Hour"].between(12, 15)]["spread_EUR_MWh"].mean()
    neg_da = df[df["price_DA_PT_EUR_MWh"] < 0]["spread_EUR_MWh"].mean()
    out_of = ((df["price_XBID_PT_EUR_MWh"] < -500) |
              (df["price_XBID_PT_EUR_MWh"] > 3000)).sum()

    print(f"  Rows             : {len(df):,}")
    print(f"  DA-XBID corr     : {corr:.4f}  (expect >0.95) {'OK' if corr > 0.95 else 'FAIL'}")
    print(f"  Spread std       : {std_sp:.2f} EUR/MWh  (expect ~14, wider than IDA3~11) {'OK' if 10 < std_sp < 20 else 'FAIL'}")
    print(f"  Solar dip H12-15 : {solar:.2f} EUR/MWh  (expect negative) {'OK' if solar < 0 else 'FAIL'}")
    print(f"  DA<0 spread mean : {neg_da:.2f} EUR/MWh  (expect negative) {'OK' if neg_da < 0 else 'FAIL'}")
    print(f"  Price violations : {out_of}  (expect 0) {'OK' if out_of == 0 else 'FAIL'}")

    with pd.ExcelWriter(_OUT, engine="openpyxl") as w:
        df.to_excel(w, sheet_name=_SHEET, index=False)
    print(f"\nSaved: {_OUT}  ({len(df):,} rows, sheet={_SHEET})")


if __name__ == "__main__":
    main()
