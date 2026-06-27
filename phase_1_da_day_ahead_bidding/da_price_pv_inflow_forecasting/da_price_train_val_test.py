"""
da_price_train_val_test.py — offline train / validation / test evaluation
for the DA price forecaster (OMIE MIBEL Portugal).

Industry MLOps pattern: evaluation pipeline is SEPARATE from the serving
path. da_price_forecaster.py retrains on all history daily and predicts
the next delivery day. This script measures *generalisation* and produces
the unbiased accuracy numbers quoted in documentation and interviews.

Protocol — all splits chronological (no shuffling, no future leakage):

    oldest ──────────────────────────────────────────► newest
    [============ DEVELOPMENT ============][==== TEST ====]
     walk-forward CV: train + select model     evaluate ONCE
                                                (untouched until here)

    1. Hold out the last TEST_MONTHS as TEST (never seen during selection)
    2. Walk-forward CV inside DEVELOPMENT → select best model  (validation)
    3. Retrain selected model on the full DEVELOPMENT set
    4. Predict TEST once → unbiased MAE / RMSE / Bias / Skill  (test)
    5. Write da_price_evaluation_report.md

Metrics:
    MAE         mean absolute error (EUR/MWh)
    RMSE        root mean squared error (penalises large misses)
    Bias (ME)   mean error = mean(pred − actual); +ve = over-forecast
    Skill       1 − MAE_model / MAE_naive  (energy-industry standard;
                >0 means the model beats naive 24h persistence)

Run:
    python phase_1_da_day_ahead_bidding/price_and_power_forecasting/da_price_train_val_test.py
"""
from __future__ import annotations

import datetime
import json
import os
import sys

import numpy as np
import pandas as pd

# Add this folder (ml_common lives here) and project root (common_layer lives there)
_HERE = os.path.dirname(__file__)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ml_train_val_test_common import fit_ridge, fit_lgbm, mae, metrics, walk_forward_cv
from da_price_forecaster import _load_history, _build_features, _feature_cols

TEST_MONTHS = 12
N_CV_FOLDS  = 4
_REPORT     = os.path.join(_HERE, "da_price_evaluation_report.md")
_JSON_PATH  = os.path.join(_HERE, "da_selected_model.json")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_da_price() -> dict:
    """Chronological dev/test split → CV selection → single unbiased test eval."""
    full = _build_features(_load_history())
    fcols = _feature_cols()
    data  = full.dropna(subset=fcols + ["price_DA_PT_EUR_MWh", "lag_24h"]).copy()
    data  = data.sort_values("datetime").reset_index(drop=True)

    last_dt    = data["datetime"].max()
    test_start = last_dt - pd.DateOffset(months=TEST_MONTHS)
    dev  = data[data["datetime"] <  test_start]
    test = data[data["datetime"] >= test_start]

    if len(dev) < 48 or len(test) == 0:
        raise ValueError("Not enough data for dev/test split")

    dev_feat  = dev[fcols]
    dev_y     = dev["price_DA_PT_EUR_MWh"].values
    dev_lag   = dev["lag_24h"].values
    test_feat = test[fcols]
    test_y    = test["price_DA_PT_EUR_MWh"].values
    test_lag  = test["lag_24h"].values

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
    else:  # Naive
        test_pred = test_lag

    # 3) Test metrics — naive baseline on SAME test window for skill score
    mae_naive   = mae(test_y, test_lag)
    test_metrics = metrics(test_y, test_pred, mae_naive)

    # Feature importance (LightGBM only)
    fi = None
    if selected == "LightGBM" and hasattr(model, "feature_importances_"):
        fi = sorted(zip(fcols, model.feature_importances_),
                    key=lambda x: x[1], reverse=True)

    # Save selection to json so da_price_forecaster picks it up without re-running CV
    excel_last = data["datetime"].max()
    _save_json(selected, cv_mae, excel_last)

    return {
        "selected"  : selected,
        "cv_mae"    : cv_mae,
        "test"      : test_metrics,
        "n_dev"     : len(dev),
        "n_test"    : len(test),
        "dev_range" : (dev["datetime"].min(),  dev["datetime"].max()),
        "test_range": (test["datetime"].min(), test["datetime"].max()),
        "feature_importance": fi,
    }


def _save_json(selected: str, cv_mae: dict, excel_last: pd.Timestamp) -> None:
    info = {
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

def _write_report(r: dict) -> None:
    u    = "EUR/MWh"
    cv   = r["cv_mae"]
    t    = r["test"]
    fi   = r["feature_importance"]

    lines = [
        "# DA Price Forecaster — Train / Validation / Test Report",
        "",
        f"_Generated: {datetime.date.today()}_",
        "",
        "## Methodology",
        "",
        "Chronological train / validation / test — no shuffling, no future leakage.",
        f"Walk-forward CV ({N_CV_FOLDS} folds) on the development set selects the model.",
        f"The final {TEST_MONTHS}-month held-out test set (never seen during selection) "
        "gives the unbiased accuracy below.",
        "The serving forecaster (da_price_forecaster.py) retrains on all history for "
        "live daily prediction.",
        "",
        "Skill score = 1 − MAE_model / MAE_naive; positive = beats naive 24h persistence.",
        "",
        "---",
        "",
        "## Data",
        "",
        f"- Development : {r['n_dev']:,} rows  "
        f"({r['dev_range'][0].date()} → {r['dev_range'][1].date()})",
        f"- Test (held out): {r['n_test']:,} rows  "
        f"({r['test_range'][0].date()} → {r['test_range'][1].date()})",
        f"- Selected model : **{r['selected']}**",
        "",
        "---",
        "",
        "## Walk-forward CV — Validation MAE (model selection)",
        "",
        f"| Model | MAE ({u}) |",
        "|-------|-----------|",
        f"| Naive persistence | {cv['Naive']:.2f} |",
        f"| Ridge regression  | {cv['Ridge']:.2f} |",
        f"| LightGBM          | {cv['LightGBM']:.2f} |",
        "",
        "---",
        "",
        "## Held-out TEST — Unbiased Accuracy",
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
            "---",
            "",
            "## Feature Importance (LightGBM — top 10)",
            "",
            "| Feature | Importance |",
            "|---------|-----------|",
        ]
        for fname, imp in fi[:10]:
            lines.append(f"| {fname} | {imp:.0f} |")
        lines.append("")

    with open(_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("\nDA Price Forecaster — Train / Validation / Test Evaluation")
    print("=" * 60)
    print(f"  Loading data and running walk-forward CV ({N_CV_FOLDS} folds)...")

    r = evaluate_da_price()
    t = r["test"]

    print(f"\n  Walk-forward CV results (validation — model selection):")
    print(f"  {'Model':<22} {'MAE (EUR/MWh)':>14}")
    print(f"  {'-'*38}")
    for name in ["Naive", "Ridge", "LightGBM"]:
        marker = " <-- selected" if name == r["selected"] else ""
        print(f"  {name:<22} {r['cv_mae'].get(name, float('inf')):>10.2f}{marker}")

    print(f"\n  Held-out TEST set ({TEST_MONTHS} months — unbiased):")
    print(f"  {'MAE':<12} {t['MAE']:.2f} EUR/MWh")
    print(f"  {'RMSE':<12} {t['RMSE']:.2f} EUR/MWh")
    print(f"  {'Bias (ME)':<12} {t['Bias']:+.2f} EUR/MWh")
    print(f"  {'Skill':<12} {t['Skill']*100:+.1f}% vs naive persistence")

    if r["feature_importance"]:
        print(f"\n  Top-5 features by importance:")
        for fname, imp in r["feature_importance"][:5]:
            print(f"    {fname:<26} {imp:.0f}")

    _write_report(r)
    print(f"\n  Report written: {os.path.basename(_REPORT)}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
