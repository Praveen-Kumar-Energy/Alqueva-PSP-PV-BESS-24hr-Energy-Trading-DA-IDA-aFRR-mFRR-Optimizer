"""
pv_train_val_test.py — offline train / validation / test evaluation for the
PV forecaster (GHI W/m² and T_amb °C, Alqueva ERA5 data 2015-2025).

Industry MLOps pattern: evaluation pipeline is SEPARATE from the serving path.
pv_power_forecaster.py retrains daily on full history — this script measures
generalisation and produces the unbiased accuracy numbers for documentation
and interviews.

Protocol — all splits chronological (no shuffling, no future leakage):

    oldest ──────────────────────────────────────────► newest
    [============ DEVELOPMENT ============][==== TEST ====]
     walk-forward CV: train + select model     evaluate ONCE
                                                (untouched until here)

    1. Hold out last TEST_MONTHS as TEST (never seen during selection)
    2. Walk-forward CV inside DEVELOPMENT → select best model  (validation)
    3. Retrain selected model on full DEVELOPMENT set
    4. Predict TEST once → unbiased MAE / RMSE / Bias / Skill  (test)
    5. Write pv_evaluation_report.md

Two targets evaluated independently:
    GHI    (W/m²)  — global horizontal irradiance
    T_amb  (°C)    — ambient temperature

Run:
    python phase_1_da_day_ahead_bidding/price_and_power_forecasting/pv_train_val_test.py
"""
from __future__ import annotations

import datetime
import json
import os
import sys

import numpy as np
import pandas as pd

# Add this folder to path so ml_train_val_test_common and pv_power_forecaster resolve
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ml_train_val_test_common import fit_ridge, fit_lgbm, mae, metrics, walk_forward_cv
from pv_power_forecaster import _load_history, _build_features, _feature_cols

TEST_MONTHS = 12
N_CV_FOLDS  = 4
_REPORT     = os.path.join(_HERE, "pv_evaluation_report.md")
_JSON_PATH  = os.path.join(_HERE, "pv_selected_model.json")


# ---------------------------------------------------------------------------
# Evaluation for one target (GHI or T_amb)
# ---------------------------------------------------------------------------

def evaluate_target(full: pd.DataFrame, target_col: str, label: str,
                    unit: str) -> dict:
    """Chronological dev/test split → CV selection → single unbiased test eval."""
    fcols   = _feature_cols(target_col)
    lag_col = f"lag_24h_{target_col}"
    data    = full.dropna(subset=fcols + [target_col, lag_col]).copy()
    data    = data.sort_values("datetime").reset_index(drop=True)

    last_dt    = data["datetime"].max()
    test_start = last_dt - pd.DateOffset(months=TEST_MONTHS)
    dev  = data[data["datetime"] <  test_start]
    test = data[data["datetime"] >= test_start]

    if len(dev) < 48 or len(test) == 0:
        raise ValueError(f"{label}: not enough data for dev/test split")

    dev_feat  = dev[fcols]
    dev_y     = dev[target_col].values
    dev_lag   = dev[lag_col].values
    test_feat = test[fcols]
    test_y    = test[target_col].values
    test_lag  = test[lag_col].values

    # 1) Walk-forward CV on DEVELOPMENT → select model
    cv_mae   = walk_forward_cv(dev_feat, dev_y, dev_lag, fcols, N_CV_FOLDS)
    selected = min(cv_mae, key=cv_mae.get)

    # 2) Retrain selected model on FULL development set
    if selected == "LightGBM":
        model     = fit_lgbm(dev_feat, dev_y, fcols)
        test_pred = model.predict(test_feat)
    elif selected == "Ridge":
        model     = fit_ridge(dev_feat.values, dev_y)
        test_pred = model.predict(test_feat.values)
    else:
        model     = None
        test_pred = test_lag

    # 3) Test metrics — naive baseline on same test window
    mae_naive    = mae(test_y, test_lag)
    test_metrics = metrics(test_y, test_pred, mae_naive)

    # Feature importance (LightGBM only)
    fi = None
    if selected == "LightGBM" and hasattr(model, "feature_importances_"):
        fi = sorted(zip(fcols, model.feature_importances_),
                    key=lambda x: x[1], reverse=True)

    # Persist selection so pv_power_forecaster skips CV on next run
    _save_json(target_col, selected, cv_mae, data["datetime"].max())

    return {
        "label"   : label,   "unit"    : unit,
        "selected": selected, "cv_mae"  : cv_mae,
        "test"    : test_metrics,
        "n_dev"   : len(dev), "n_test"  : len(test),
        "dev_range" : (dev["datetime"].min(),  dev["datetime"].max()),
        "test_range": (test["datetime"].min(), test["datetime"].max()),
        "feature_importance": fi,
    }


def _save_json(target_col: str, selected: str, cv_mae: dict,
               excel_last: pd.Timestamp) -> None:
    info: dict = {}
    if os.path.exists(_JSON_PATH):
        with open(_JSON_PATH, "r") as f:
            info = json.load(f)
    info[target_col] = {
        "selected"     : selected,
        "cv_mae"       : {k: round(v, 4) for k, v in cv_mae.items()},
        "data_end_date": str(excel_last.date()),
        "updated_on"   : str(datetime.date.today()),
    }
    with open(_JSON_PATH, "w") as f:
        json.dump(info, f, indent=2)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt_result(r: dict) -> str:
    u  = r["unit"]
    cv = r["cv_mae"]
    t  = r["test"]
    fi = r["feature_importance"]

    lines = [
        f"### {r['label']}",
        "",
        f"- Development  : {r['n_dev']:,} rows "
        f"({r['dev_range'][0].date()} → {r['dev_range'][1].date()})",
        f"- Test (held out): {r['n_test']:,} rows "
        f"({r['test_range'][0].date()} → {r['test_range'][1].date()})",
        f"- Selected model : **{r['selected']}**",
        "",
        "**Walk-forward CV (validation MAE — model selection):**",
        "",
        f"| Model | MAE ({u}) |",
        "|-------|-----------|",
        f"| Naive persistence | {cv['Naive']:.2f} |",
        f"| Ridge regression  | {cv['Ridge']:.2f} |",
        f"| LightGBM          | {cv['LightGBM']:.2f} |",
        "",
        "**Held-out TEST (unbiased):**",
        "",
        f"| Metric | Value |",
        "|--------|-------|",
        f"| MAE        | {t['MAE']:.2f} {u} |",
        f"| RMSE       | {t['RMSE']:.2f} {u} |",
        f"| Bias (ME)  | {t['Bias']:+.2f} {u} |",
        f"| Skill vs naive | {t['Skill']*100:+.1f}% |",
        "",
    ]
    if fi:
        lines += [
            "**Feature Importance (LightGBM — top 10):**",
            "",
            f"| Feature | Importance |",
            "|---------|-----------|",
        ]
        for fname, imp in fi[:10]:
            lines.append(f"| {fname} | {imp:.0f} |")
        lines.append("")

    return "\n".join(lines)


def _write_report(results: list) -> None:
    header = [
        "# PV Forecaster — Train / Validation / Test Report",
        "",
        f"_Generated: {datetime.date.today()}_",
        "",
        "## Methodology",
        "",
        "Chronological train / validation / test — no shuffling, no future leakage.",
        f"Walk-forward CV ({N_CV_FOLDS} folds) on development set selects the model.",
        f"Final {TEST_MONTHS}-month held-out test set (never seen during selection) "
        "gives unbiased accuracy below.",
        "Serving forecaster (pv_power_forecaster.py) retrains on all history daily.",
        "",
        "Skill score = 1 − MAE_model / MAE_naive; positive = beats naive 24h persistence.",
        "",
        "---",
        "",
    ]
    body = "\n---\n\n".join(_fmt_result(r) for r in results)
    with open(_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(header) + body)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("\nPV Forecaster — Train / Validation / Test Evaluation")
    print("=" * 60)
    print("  Loading ERA5 data and building features...")

    full = _build_features(_load_history())
    results = []

    for target_col, label, unit in [
        ("GHI",   "PV — Global Horizontal Irradiance", "W/m2"),
        ("T_amb", "PV — Ambient Temperature",          "degC"),
    ]:
        print(f"\n  Evaluating {label}...")
        r = evaluate_target(full, target_col, label, unit)
        results.append(r)

        t = r["test"]
        print(f"  Walk-forward CV:")
        print(f"    {'Model':<22} {'MAE (' + unit + ')':>14}")
        print(f"    {'-'*38}")
        for name in ["Naive", "Ridge", "LightGBM"]:
            marker = " <-- selected" if name == r["selected"] else ""
            print(f"    {name:<22} {r['cv_mae'].get(name, float('inf')):>10.2f}{marker}")
        print(f"\n  Held-out TEST:")
        print(f"    MAE    {t['MAE']:.2f} {unit}")
        print(f"    RMSE   {t['RMSE']:.2f} {unit}")
        print(f"    Bias   {t['Bias']:+.2f} {unit}")
        print(f"    Skill  {t['Skill']*100:+.1f}% vs naive persistence")

        if r["feature_importance"]:
            print(f"\n  Top-5 features ({target_col}):")
            for fname, imp in r["feature_importance"][:5]:
                print(f"    {fname:<28} {imp:.0f}")

    _write_report(results)
    print(f"\n  Report written: {os.path.basename(_REPORT)}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
