"""
ml_train_val_test_common.py — shared ML utilities for Phase 1 forecasters.

Used by:
    da_price_forecaster.py        (serving)
    pv_power_forecaster.py        (serving)
    da_price_train_val_test.py    (offline evaluation)
    pv_train_val_test.py          (offline evaluation — future)

Single source of truth: fix here, all consumers benefit.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Model fitting
# ---------------------------------------------------------------------------

def fit_ridge(X: np.ndarray, y: np.ndarray):
    """Ridge regression with standard scaling. X must be numpy array."""
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    model = Pipeline([("scaler", StandardScaler()), ("ridge", Ridge(alpha=1.0))])
    model.fit(X, y)
    return model


def fit_lgbm(X: pd.DataFrame, y: np.ndarray, feature_names: List[str]):
    """LightGBM regressor optimised for energy time-series (MAE objective)."""
    import lightgbm as lgb
    model = lgb.LGBMRegressor(
        objective         = "regression",
        metric            = "mae",
        num_leaves        = 64,
        learning_rate     = 0.05,
        feature_fraction  = 0.8,
        bagging_fraction  = 0.8,
        bagging_freq      = 5,
        min_child_samples = 20,
        lambda_l1         = 0.1,
        lambda_l2         = 0.1,
        n_estimators      = 500,
        verbose           = -1,
    )
    model.fit(X, y, feature_name=feature_names)
    return model


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(y_true - y_pred)))


def metrics(y_true: np.ndarray, y_pred: np.ndarray,
            mae_naive: float) -> Dict[str, float]:
    """MAE, RMSE, Bias (ME), Skill vs naive persistence."""
    err   = y_pred - y_true
    _mae  = float(np.mean(np.abs(err)))
    rmse  = float(np.sqrt(np.mean(err ** 2)))
    bias  = float(np.mean(err))
    skill = float(1.0 - _mae / mae_naive) if mae_naive > 0 else float("nan")
    return {"MAE": _mae, "RMSE": rmse, "Bias": bias, "Skill": skill}


# ---------------------------------------------------------------------------
# Walk-forward cross-validation (model selection / validation role)
# ---------------------------------------------------------------------------

def walk_forward_cv(feat_df: pd.DataFrame, y: np.ndarray, lag: np.ndarray,
                    fcols: List[str], n_folds: int) -> Dict[str, float]:
    """Compare Naive / Ridge / LightGBM via walk-forward CV.

    Each fold trains on all prior data, validates on the next block —
    no future leakage. Returns mean MAE per model across folds.
    """
    n       = len(feat_df)
    fold_sz = n // (n_folds + 1)
    fold_mae: Dict[str, list] = {"Naive": [], "Ridge": [], "LightGBM": []}

    for fold in range(n_folds):
        tr_end  = fold_sz * (fold + 1)
        val_end = tr_end + fold_sz
        X_tr    = feat_df.iloc[:tr_end]
        X_val   = feat_df.iloc[tr_end:val_end]
        y_tr    = y[:tr_end]
        y_val   = y[tr_end:val_end]
        lag_val = lag[tr_end:val_end]

        if len(X_tr) < 48 or len(X_val) == 0:
            continue

        fold_mae["Naive"].append(mae(y_val, lag_val))

        ridge = fit_ridge(X_tr.values, y_tr)
        fold_mae["Ridge"].append(mae(y_val, ridge.predict(X_val.values)))

        lgbm = fit_lgbm(X_tr, y_tr, fcols)
        fold_mae["LightGBM"].append(mae(y_val, lgbm.predict(X_val)))

    return {k: float(np.mean(v)) if v else float("inf")
            for k, v in fold_mae.items()}
