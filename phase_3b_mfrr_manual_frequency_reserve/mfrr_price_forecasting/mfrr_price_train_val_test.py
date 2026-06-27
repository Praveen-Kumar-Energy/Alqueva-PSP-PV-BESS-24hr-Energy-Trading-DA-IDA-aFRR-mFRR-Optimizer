"""
mfrr_price_train_val_test.py — offline evaluation of the mFRR cap-price models.

Two models evaluated separately (cap_up and cap_dn):
    - Walk-forward CV (3 folds) on the training window (2024-11-27 to 2025-09-30)
    - Hold-out TEST on 2025-10-01 to 2025-12-31 (3 months)
    - Per-hour-bucket breakdown: peak vs off-peak

Note: only ~13 months of data (REN joined MARI 2024-11-27). 3 CV folds used
instead of 4. Test window is 3 months (shorter than aFRR's full year).

Naive baseline: predict the training-set mean (flat forecast).
Skill score = 1 - MAE_model / MAE_naive

Run:
    python phase_3b_mfrr_manual_frequency_reserve/mfrr_price_forecasting/mfrr_price_train_val_test.py
"""
from __future__ import annotations

import datetime
import json
import os
import sys

import numpy as np
import pandas as pd

_HERE     = os.path.dirname(os.path.abspath(__file__))
_REPO     = os.path.abspath(os.path.join(_HERE, "..", ".."))
_FCST_DIR = os.path.join(_REPO, "phase_1_da_day_ahead_bidding", "price_and_power_forecasting")

sys.path.insert(0, _REPO)
sys.path.insert(0, _FCST_DIR)
from ml_train_val_test_common import fit_ridge, fit_lgbm, mae as _mae, walk_forward_cv

_EXCEL_PATH = os.path.join(_HERE, "mfrr_training_data_2024_2025.xlsx")
_SHEET      = "MFRR_2024_2025"
_JSON_UP    = os.path.join(_HERE, "mfrr_up_selected_model.json")
_JSON_DN    = os.path.join(_HERE, "mfrr_dn_selected_model.json")
_REPORT     = os.path.join(_HERE, "mfrr_price_evaluation_report.md")
_N_FOLDS    = 3
_TEST_START = "2025-10-01"   # 3-month hold-out (limited data: 13 months total)


def _feature_cols():
    return [
        "hour_sin", "hour_cos",
        "dow_sin", "dow_cos",
        "month_sin", "month_cos",
        "is_weekend",
        "da_price",
        "da_roll_mean_24h", "da_roll_std_24h",
        "cap_up_lag_168h",
        "cap_dn_lag_168h",
    ]


def _load_features() -> pd.DataFrame:
    df = pd.read_excel(_EXCEL_PATH, sheet_name=_SHEET)
    df.columns = [c.strip() for c in df.columns]
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.rename(columns={"Hour": "hour", "price_DA_PT_EUR_MWh": "da_price"})
    df = df.sort_values(["Date", "hour"]).reset_index(drop=True)

    df["hour_sin"]   = np.sin(2 * np.pi * (df["hour"] - 1) / 24)
    df["hour_cos"]   = np.cos(2 * np.pi * (df["hour"] - 1) / 24)
    dow              = df["Date"].dt.dayofweek
    month            = df["Date"].dt.month
    df["dow_sin"]    = np.sin(2 * np.pi * dow / 7)
    df["dow_cos"]    = np.cos(2 * np.pi * dow / 7)
    df["month_sin"]  = np.sin(2 * np.pi * (month - 1) / 12)
    df["month_cos"]  = np.cos(2 * np.pi * (month - 1) / 12)
    df["is_weekend"] = (dow >= 5).astype(int)

    grp = df.groupby("Date", sort=False)
    df["da_roll_mean_24h"] = grp["da_price"].transform(
        lambda x: x.rolling(min(24, len(x)), min_periods=1).mean())
    df["da_roll_std_24h"]  = grp["da_price"].transform(
        lambda x: x.rolling(min(24, len(x)), min_periods=2).std().fillna(0.0))

    lag_df = df[["Date", "hour", "cap_up_EUR_MW", "cap_dn_EUR_MW"]].copy()
    lag_df["Date"] = lag_df["Date"] + pd.Timedelta(days=7)
    lag_df = lag_df.rename(columns={"cap_up_EUR_MW": "cap_up_lag_168h",
                                     "cap_dn_EUR_MW": "cap_dn_lag_168h"})
    df = df.merge(lag_df, on=["Date", "hour"], how="left")
    return df.dropna()


def _eval_one(label: str, target_col: str, json_path: str,
              train: pd.DataFrame, test: pd.DataFrame,
              fcols: list) -> dict:
    X_tr = train[fcols]
    y_tr = train[target_col].values
    naive_tr = np.full_like(y_tr, y_tr.mean())

    print(f"\n--- {label} ---")
    print("Running walk-forward CV ...")
    cv_mae   = walk_forward_cv(X_tr, y_tr, naive_tr, fcols, _N_FOLDS)
    selected = min(cv_mae, key=cv_mae.get)
    for name in ["Naive", "Ridge", "LightGBM"]:
        marker = "  <-- SELECTED" if name == selected else ""
        print(f"  {name:<22} {cv_mae.get(name, float('inf')):.4f}{marker}")

    X_te      = test[fcols]
    y_te      = test[target_col].values
    naive_val = y_tr.mean()

    if selected == "LightGBM":
        model = fit_lgbm(X_tr, y_tr, fcols)
        preds = model.predict(X_te)
    elif selected == "Ridge":
        model = fit_ridge(X_tr.values, y_tr)
        preds = model.predict(X_te.values)
    else:
        preds = np.full_like(y_te, naive_val)

    test_mae  = _mae(y_te, preds)
    naive_mae = _mae(y_te, np.full_like(y_te, naive_val))
    skill     = 1 - test_mae / naive_mae if naive_mae > 0 else 0.0

    print(f"Test (Oct-Dec 2025): Naive MAE={naive_mae:.4f}  {selected}={test_mae:.4f}  skill={skill:+.1%}")

    test = test.copy()
    test["pred"] = preds
    buckets = {
        "Off-peak (H1-H6, H23-H24)": list(range(1, 7)) + [23, 24],
        "Peak     (H7-H22)         ": list(range(7, 23)),
    }
    for blabel, hrs in buckets.items():
        sub = test[test["hour"].isin(hrs)]
        if len(sub) == 0:
            continue
        m_n = _mae(sub[target_col].values, np.full(len(sub), naive_val))
        m_m = _mae(sub[target_col].values, sub["pred"].values)
        sk  = 1 - m_m / m_n if m_n > 0 else 0.0
        print(f"  {blabel}  Naive={m_n:.2f}  {selected}={m_m:.2f}  skill={sk:+.1%}")

    excel_last = train["Date"].max().date()
    with open(json_path, "w") as f:
        json.dump({"selected": selected,
                   "cv_mae": {k: round(v, 4) for k, v in cv_mae.items()},
                   "data_end_date": str(excel_last),
                   "updated_on": str(datetime.date.today())}, f, indent=2)
    print(f"Saved: {json_path}")

    return {"selected": selected, "cv_mae": cv_mae,
            "test_mae": test_mae, "naive_mae": naive_mae, "skill": skill,
            "test_df": test, "naive_val": naive_val}


def evaluate_mfrr() -> None:
    print("Loading mFRR features ...")
    df = _load_features()
    print(f"  Rows : {len(df):,}  ({df['Date'].min().date()} to {df['Date'].max().date()})")

    train = df[df["Date"] <  _TEST_START].copy()
    test  = df[df["Date"] >= _TEST_START].copy()
    print(f"  Train: {len(train):,} rows | Test: {len(test):,} rows (Oct-Dec 2025)")

    fcols  = _feature_cols()
    res_up = _eval_one("cap_up (EUR/MW)", "cap_up_EUR_MW", _JSON_UP, train, test, fcols)
    res_dn = _eval_one("cap_dn (EUR/MW)", "cap_dn_EUR_MW", _JSON_DN, train, test, fcols)

    _write_report(res_up, res_dn, df["Date"].max().date())


def _write_report(res_up: dict, res_dn: dict, excel_last) -> None:
    lines = [
        "# mFRR Cap-Price Forecaster — Evaluation Report",
        "",
        f"Generated: {datetime.date.today()}",
        "",
        "## Data",
        "- Source: `mfrr_training_data_2024_2025.xlsx` (MARI mFRR clearing prices — synthetic proxy)",
        f"- Range : 2024-11-27 (REN MARI accession) to {excel_last}",
        "- Gate  : mFRR capacity market (H1-H24, daily auction, gate closes D-1 before DA)",
        "- Models: two separate models — cap_up (upward reserve) and cap_dn (downward reserve)",
        "- Note  : 13 months data; 3 CV folds; 3-month hold-out test (Oct-Dec 2025)",
        "",
    ]
    for label, res in [("cap_up", res_up), ("cap_dn", res_dn)]:
        sel = res["selected"]
        lines += [
            f"## {label} Model",
            f"| Model | CV MAE EUR/MW |",
            "|---|---|",
        ]
        for name in ["Naive", "Ridge", "LightGBM"]:
            marker = " **SELECTED**" if name == sel else ""
            lines.append(f"| {name} | {res['cv_mae'].get(name, float('inf')):.4f}{marker} |")
        lines += [
            "",
            f"| Metric | Value |",
            "|---|---|",
            f"| Naive MAE | {res['naive_mae']:.4f} EUR/MW |",
            f"| {sel} MAE | {res['test_mae']:.4f} EUR/MW |",
            f"| Skill score | {res['skill']:+.1%} |",
            "",
        ]

    lines += [
        "## mFRR Gate (Production)",
        "- Daily capacity auction: offer submitted D-1 before DA gate (gate closes ~D-1 08:00 CET)",
        "- FAT: 12.5 minutes (MARI harmonised, REN joined MARI 27 Nov 2024)",
        "- Platform: MARI (Manually Activated Reserves Initiative)",
        "- mFRR sized from headroom REMAINING after aFRR commitment (PR-11 stack)",
        "- No MW sold twice: mFRR + aFRR headroom bounded by energy position",
        "- Cap ceiling: 250 EUR/MW (REN regulatory cap)",
        "- Independent forecast (not derived from aFRR): MARI and PICASSO are separate markets",
    ]

    with open(_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nReport: {_REPORT}")


if __name__ == "__main__":
    evaluate_mfrr()
