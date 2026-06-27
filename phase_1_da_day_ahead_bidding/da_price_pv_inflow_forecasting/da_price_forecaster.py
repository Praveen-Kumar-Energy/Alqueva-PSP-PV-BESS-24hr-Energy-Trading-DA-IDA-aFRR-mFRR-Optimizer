"""
da_price_forecaster.py — DA price forecast for a delivery day.

Methodology:
    1. Load da_training_data_2020_2026.xlsx (historical OMIE MIBEL DA prices, PT)
    2. Build lag + calendar features
    3. Auto-select best model via da_selected_model.json:
         - On first run OR when Excel has new data since last evaluation:
             walk-forward CV (4 folds) compares Naive / Ridge / LightGBM
             → updates da_selected_model.json automatically
         - Otherwise: reads selected model from json (no CV overhead)
    4. Train selected model on ALL history
    5. Predict delivery_date 24 hours

Auto-update trigger: Excel data_end_date > json data_end_date
→ new data arrived → re-run CV → update selection automatically.

Model and data cached after first call — no retraining on repeated calls
within the same Python session.

Fallback: if Excel missing or history too short, returns a deterministic
synthetic Iberian-shaped curve so the pipeline never crashes.

Reference: Lago et al. (2021) "Forecasting day-ahead electricity prices:
A review of state-of-the-art algorithms, best practices and an open-access
benchmark" — confirms gradient boosting + lag features best on EPEX/OMIE.

Returned shape: {hour: EUR/MWh} for hours 1..24.
Floor: -500 EUR/MWh (OMIE SDAC regulatory minimum, effective Jan 2023).
"""
from __future__ import annotations

import datetime
import json
import math
import os
import random
import sys
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# Ensure this folder is on sys.path so ml_train_val_test_common resolves
# whether this module is run directly or imported from run_da.py / run_production.py
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ml_train_val_test_common import fit_ridge, fit_lgbm, mae as _mae, walk_forward_cv

_EXCEL_PATH   = os.path.join(_HERE, "da_training_data_2020_2026.xlsx")
_JSON_PATH    = os.path.join(_HERE, "da_selected_model.json")
_WARMUP_HOURS = 336
_N_CV_FOLDS   = 4
_cache: dict  = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def forecast_da_prices(hours: List[int], delivery_date: str,
                       mean_level: float = 55.0,
                       amplitude: float  = 22.0) -> Dict[int, float]:
    """Return {hour: EUR/MWh} for the 24 hours of delivery_date.

    Trains LightGBM (or best CV model) live on OMIE historical data.
    Falls back to synthetic Iberian curve if data unavailable.
    """
    try:
        return _model_forecast(hours, delivery_date)
    except Exception as exc:
        import warnings
        warnings.warn(
            f"[DA Forecaster] Model failed ({exc}); using synthetic fallback. "
            f"Check {_EXCEL_PATH} and dependencies.",
            RuntimeWarning, stacklevel=2
        )
        return _synthetic_fallback(hours, delivery_date, mean_level, amplitude)


# ---------------------------------------------------------------------------
# Core forecasting pipeline
# ---------------------------------------------------------------------------

def _model_forecast(hours: List[int], delivery_date: str) -> Dict[int, float]:
    df        = _load_history()
    target_dt = pd.Timestamp(delivery_date)
    cutoff    = target_dt - pd.Timedelta(hours=1)

    # Drop any pre-existing delivery-date (or future) rows so the appended
    # placeholder is the only set of delivery-date rows. A prior data update for
    # a later date can otherwise leave duplicate rows that corrupt the lag /
    # rolling features and force the synthetic fallback on every run.
    df = df[df["datetime"] < target_dt].copy()

    history = df[df["datetime"] <= cutoff]
    if len(history) < _WARMUP_HOURS:
        raise ValueError(f"Insufficient history ({len(history)} rows)")

    # Append 24 placeholder rows for delivery_date.
    # Prices initialised to Naive (lag_24h = same hour yesterday) so that
    # rolling features (roll_mean_24h etc.) stay non-NaN for hours H2..H24.
    # The actual prediction overwrites these values — they are never used as targets.
    lag24 = df[df["datetime"].dt.date == (target_dt - pd.Timedelta(days=1)).date()
               ].set_index("hour")["price_DA_PT_EUR_MWh"]
    naive_prices = [float(lag24.get(h, lag24.mean())) for h in range(1, 25)]
    placeholder = pd.DataFrame({
        "datetime"            : [target_dt + pd.Timedelta(hours=h - 1) for h in range(1, 25)],
        "hour"                : list(range(1, 25)),
        "price_DA_PT_EUR_MWh" : naive_prices,
    })
    df_ext   = pd.concat([df, placeholder], ignore_index=True).sort_values("datetime")
    full     = _build_features(df_ext)
    train_df = full[full["datetime"] <= cutoff].dropna()
    pred_df  = full[full["datetime"].dt.date == target_dt.date()].copy()

    if pred_df.empty:
        raise ValueError(f"No feature rows for {delivery_date}")

    cache_key = f"model_{delivery_date}"
    if cache_key not in _cache:
        # Auto-select model (CV only when new data arrived)
        selected = _auto_select_model(train_df)

        fcols = _feature_cols()
        X     = train_df[fcols]
        y     = train_df["price_DA_PT_EUR_MWh"].values

        if selected == "LightGBM":
            model = fit_lgbm(X, y, fcols)
        elif selected == "Ridge":
            model = fit_ridge(X.values, y)
        else:
            model = None  # Naive

        _cache[cache_key]            = model
        _cache[f"sel_{delivery_date}"] = selected

    model    = _cache[cache_key]
    selected = _cache[f"sel_{delivery_date}"]
    X_pred   = pred_df[_feature_cols()]

    if model is None:
        preds = pred_df["lag_24h"].values
    elif selected == "Ridge":
        preds = model.predict(X_pred.values)
    else:
        preds = model.predict(X_pred)

    pred_df = pred_df.copy()
    pred_df["pred"] = preds
    lookup = dict(zip(pred_df["hour"].astype(int), pred_df["pred"]))
    return {h: round(max(-500.0, float(lookup[h])), 2)
            for h in hours if h in lookup}


# ---------------------------------------------------------------------------
# Auto model selection — CV only when new data arrives
# ---------------------------------------------------------------------------

def _auto_select_model(train_df: pd.DataFrame) -> str:
    """Return selected model name. Re-runs CV only when Excel has new data.

    Flow:
        Read da_selected_model.json
        → data_end_date matches current Excel? → return cached selection
        → new data arrived?                    → re-run CV → update json
        → json missing?                        → run CV → create json
    """
    excel_last_date = train_df["datetime"].max().date()

    # Read existing json
    if os.path.exists(_JSON_PATH):
        with open(_JSON_PATH, "r") as f:
            info = json.load(f)
        saved_end = pd.Timestamp(info.get("data_end_date", "2000-01-01")).date()
        if saved_end >= excel_last_date:
            return info["selected"]   # still current — no CV needed

    # New data arrived (or first run) → re-run walk-forward CV
    fcols   = _feature_cols()
    feat_df = train_df[fcols]
    y       = train_df["price_DA_PT_EUR_MWh"].values
    lag24   = train_df["lag_24h"].values

    cv_mae   = walk_forward_cv(feat_df, y, lag24, fcols, _N_CV_FOLDS)
    selected = min(cv_mae, key=cv_mae.get)

    # Save to json
    info = {
        "selected"     : selected,
        "cv_mae"       : {k: round(v, 4) for k, v in cv_mae.items()},
        "data_end_date": str(excel_last_date),
        "updated_on"   : str(datetime.date.today()),
    }
    with open(_JSON_PATH, "w") as f:
        json.dump(info, f, indent=2)

    print(f"\n[DA Forecaster] Model selection updated → {selected}")
    print(f"  Data up to : {excel_last_date}")
    for name in ["Naive", "Ridge", "LightGBM"]:
        marker = " <-- selected" if name == selected else ""
        print(f"  {name:<22} MAE {cv_mae.get(name, float('inf')):.2f} EUR/MWh{marker}")
    print()

    return selected



# ---------------------------------------------------------------------------
# Data loading and feature engineering
# ---------------------------------------------------------------------------

def _load_history() -> pd.DataFrame:
    mtime = os.path.getmtime(_EXCEL_PATH)
    if "history" not in _cache or _cache.get("history_mtime") != mtime:
        df = pd.read_excel(_EXCEL_PATH, sheet_name="DA_Price_2020_2026")
        df.columns = [c.strip() for c in df.columns]
        hour_col = next(c for c in df.columns if "hour" in c.lower())
        pt_col   = next(c for c in df.columns if "PT" in c or "price_DA" in c.lower())
        df = df.rename(columns={hour_col: "hour", pt_col: "price_DA_PT_EUR_MWh"})
        df["Date"]     = pd.to_datetime(df["Date"])
        df["datetime"] = df["Date"] + pd.to_timedelta(df["hour"] - 1, unit="h")
        df = df.sort_values("datetime").reset_index(drop=True)
        _cache["history"]       = df
        _cache["history_mtime"] = mtime
    return _cache["history"]


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["datetime", "hour", "price_DA_PT_EUR_MWh"]].copy()
    p   = out["price_DA_PT_EUR_MWh"]

    # Calendar features (cyclical encoding)
    out["hour_sin"]   = np.sin(2 * np.pi * (out["hour"] - 1) / 24)
    out["hour_cos"]   = np.cos(2 * np.pi * (out["hour"] - 1) / 24)
    out["dow"]        = out["datetime"].dt.dayofweek
    out["month"]      = out["datetime"].dt.month
    out["is_weekend"] = (out["dow"] >= 5).astype(int)
    out["dow_sin"]    = np.sin(2 * np.pi * out["dow"] / 7)
    out["dow_cos"]    = np.cos(2 * np.pi * out["dow"] / 7)
    out["month_sin"]  = np.sin(2 * np.pi * (out["month"] - 1) / 12)
    out["month_cos"]  = np.cos(2 * np.pi * (out["month"] - 1) / 12)

    # Price lag features (no future leakage — all shifted by ≥24h)
    out["lag_24h"]  = p.shift(24)
    out["lag_48h"]  = p.shift(48)
    out["lag_168h"] = p.shift(168)
    out["lag_336h"] = p.shift(336)

    # Rolling statistics (shift(1) ensures no same-hour leakage)
    out["roll_mean_24h"]  = p.shift(1).rolling(24).mean()
    out["roll_std_24h"]   = p.shift(1).rolling(24).std()
    out["roll_mean_168h"] = p.shift(1).rolling(168).mean()

    # Trend signal
    out["price_diff_24h"] = p.shift(24) - p.shift(48)

    return out


def _feature_cols() -> List[str]:
    return [
        "hour_sin", "hour_cos", "dow", "month", "is_weekend",
        "dow_sin", "dow_cos", "month_sin", "month_cos",
        "lag_24h", "lag_48h", "lag_168h", "lag_336h",
        "roll_mean_24h", "roll_std_24h", "roll_mean_168h",
        "price_diff_24h",
    ]


# ---------------------------------------------------------------------------
# Synthetic fallback (deterministic Iberian-shaped curve)
# ---------------------------------------------------------------------------

def _synthetic_fallback(hours: List[int], delivery_date: str,
                         mean_level: float, amplitude: float) -> Dict[int, float]:
    rng = random.Random(f"da-{delivery_date}")
    prices = {}
    for h in hours:
        morning   = math.exp(-((h - 9)  ** 2) / 8.0)
        evening   = math.exp(-((h - 20) ** 2) / 6.0)
        solar_dip = -0.6 * math.exp(-((h - 14) ** 2) / 10.0)
        shape     = morning + 1.15 * evening + solar_dip
        night     = -0.8 if h <= 6 or h >= 23 else 0.0
        noise     = rng.uniform(-3.0, 3.0)
        prices[h] = round(mean_level + amplitude * (shape + night) + noise, 2)
    return prices
