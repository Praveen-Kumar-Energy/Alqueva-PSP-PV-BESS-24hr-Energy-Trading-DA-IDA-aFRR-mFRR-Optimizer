"""
reservoir_inflow_forecaster.py — daily natural inflow forecast for Alqueva upper reservoir.

Methodology:
    1. Load inflow_training_data_2015_2025.xlsx (daily mean m3/h, Guadiana river)
       Source: 11-year hourly research dataset (2015-2025), aggregated to daily means
       to remove sub-daily simulation noise.
    2. Gap fill: missing dates filled with monthly climatological mean from plant.yaml
    3. Build lag + seasonal features
    4. Auto-select best model via inflow_selected_model.json:
         - On first run OR when Excel has new data since last evaluation:
             walk-forward CV (4 folds) compares Naive / Ridge / LightGBM
             → updates inflow_selected_model.json automatically
         - Otherwise: reads selected model from json (no CV overhead)
    5. Forecast delivery_date daily mean inflow → distribute flat across 24 hours

Auto-update trigger: Excel data_end_date > json data_end_date
→ new data arrived → re-run CV → update selection automatically.

Returned shape: {hour: m3/h} for hours 1..24, same value each hour (daily mean).
Feeds into ReservoirFlows.inflow_m3h in reservoir_model.py.

Fallback: if Excel missing or history too short, returns monthly climatological
mean from plant.yaml so the pipeline never crashes.

Reference: Kisi (2011) "River flow forecasting and estimation using different
artificial neural network techniques" — lag features on daily streamflow standard
for short-term river flow forecasting.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from typing import Dict, List

import numpy as np  # noqa: F401 — used in _build_features seasonal encoding
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ml_train_val_test_common import fit_ridge, fit_lgbm, mae as _mae, walk_forward_cv

from common_layer.configuration.plant_config import ReservoirConfig

_EXCEL_PATH   = os.path.join(_HERE, "inflow_training_data_2015_2025.xlsx")
_SHEET        = "Inflow_2015_2025"
_JSON_PATH    = os.path.join(_HERE, "inflow_selected_model.json")
_WARMUP_DAYS  = 60    # minimum history before first CV fold
_N_CV_FOLDS   = 4
_cache: dict  = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def forecast_inflow(hours: List[int], delivery_date: str,
                    res_cfg: ReservoirConfig) -> Dict[int, float]:
    """Return {hour: m3/h} for all hours of delivery_date.

    Trains ML model on daily inflow history (daily mean, flat across 24h).
    Falls back to monthly climatological mean from plant.yaml if data unavailable.
    """
    try:
        return _model_forecast(hours, delivery_date, res_cfg)
    except Exception as exc:
        import warnings
        warnings.warn(
            f"[Inflow Forecaster] Model failed ({exc}); using climatology fallback.",
            RuntimeWarning, stacklevel=2
        )
        return _climatology_fallback(hours, delivery_date, res_cfg)


# ---------------------------------------------------------------------------
# Core forecasting pipeline
# ---------------------------------------------------------------------------

def _model_forecast(hours: List[int], delivery_date: str,
                    res_cfg: ReservoirConfig) -> Dict[int, float]:
    _fill_gaps(delivery_date, res_cfg)

    df        = _load_history()
    target_dt = pd.Timestamp(delivery_date)
    cutoff    = target_dt - pd.Timedelta(days=1)

    # Keep only history strictly before the delivery date. A previous _fill_gaps
    # run for a LATER date may already have written a climatology row for
    # target_dt (or beyond); without this, the appended NaN placeholder would
    # create a duplicate-date row and corrupt the positional lag/rolling
    # features, forcing the climatology fallback on every run.
    df = df[df["Date"] < target_dt].copy()

    train_df = df[df["Date"] <= cutoff].copy()
    if len(train_df) < _WARMUP_DAYS:
        raise ValueError(f"Insufficient history ({len(train_df)} days)")

    # Append placeholder row for delivery_date (inflow=NaN, to be predicted).
    # _build_features uses shift() so lag columns fill correctly from history.
    placeholder = pd.DataFrame({
        "Date"       : [target_dt],
        "inflow_m3h" : [np.nan],
        "source"     : ["PLACEHOLDER"],
    })
    df_ext  = pd.concat([df, placeholder], ignore_index=True).sort_values("Date")
    full    = _build_features(df_ext)
    feat_tr = full[full["Date"] <= cutoff].dropna()
    feat_pr = full[full["Date"] == target_dt].copy()

    if feat_pr.empty:
        raise ValueError(f"No feature row for {delivery_date}")

    cache_key = f"inflow_{delivery_date}"
    if cache_key not in _cache:
        selected = _auto_select_model(feat_tr)

        fcols = _feature_cols()
        X     = feat_tr[fcols]
        y     = feat_tr["inflow_m3h"].values

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
    X_pred   = feat_pr[_feature_cols()]

    if model is None:
        pred = float(feat_pr["lag_1d"].iloc[0])
    elif selected == "Ridge":
        pred = float(model.predict(X_pred.values)[0])
    else:
        pred = float(model.predict(X_pred)[0])

    pred = max(0.0, round(pred, 1))
    return {h: pred for h in hours}


# ---------------------------------------------------------------------------
# Auto model selection — CV only when new data arrives
# ---------------------------------------------------------------------------

def _auto_select_model(feat_tr: pd.DataFrame) -> str:
    """Return selected model name. Re-runs CV only when Excel has new data."""
    excel_last_date = feat_tr["Date"].max().date()

    if os.path.exists(_JSON_PATH):
        with open(_JSON_PATH, "r") as f:
            info = json.load(f)
        saved_end = pd.Timestamp(info.get("data_end_date", "2000-01-01")).date()
        if saved_end >= excel_last_date:
            return info["selected"]

    # New data or first run → walk-forward CV
    fcols   = _feature_cols()
    feat_df = feat_tr[fcols]
    y       = feat_tr["inflow_m3h"].values
    lag1    = feat_tr["lag_1d"].values

    cv_mae   = walk_forward_cv(feat_df, y, lag1, fcols, _N_CV_FOLDS)
    selected = min(cv_mae, key=cv_mae.get)

    info = {
        "selected"     : selected,
        "cv_mae"       : {k: round(v, 1) for k, v in cv_mae.items()},
        "data_end_date": str(excel_last_date),
        "updated_on"   : str(datetime.date.today()),
    }
    with open(_JSON_PATH, "w") as f:
        json.dump(info, f, indent=2)

    print(f"\n[Inflow Forecaster] Model selection updated → {selected}")
    print(f"  Data up to : {excel_last_date}")
    for name in ["Naive", "Ridge", "LightGBM"]:
        marker = " <-- selected" if name == selected else ""
        print(f"  {name:<22} MAE {cv_mae.get(name, float('inf')):.0f} m3/h{marker}")
    print()

    return selected


# ---------------------------------------------------------------------------
# Gap fill — missing dates → monthly climatological mean
# ---------------------------------------------------------------------------

def _fill_gaps(delivery_date: str, res_cfg: ReservoirConfig) -> None:
    """Fill Excel with monthly climatological mean for any missing dates up to yesterday."""
    target_dt = pd.Timestamp(delivery_date)
    yesterday = target_dt - pd.Timedelta(days=1)

    df      = _load_excel()
    last_dt = df["Date"].max() if not df.empty else pd.Timestamp("2014-12-31")

    if last_dt >= yesterday:
        return

    missing = pd.date_range(start=last_dt + pd.Timedelta(days=1),
                            end=yesterday, freq="D")
    new_rows = []
    for dt in missing:
        mean = res_cfg.inflow_for_month(dt.month)
        new_rows.append({"Date": dt, "inflow_m3h": mean, "source": "CLIMATOLOGY"})

    updated = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    updated = updated.sort_values("Date").reset_index(drop=True)
    _save_excel(updated)
    _cache.pop("inflow_history", None)


# ---------------------------------------------------------------------------
# Data loading and feature engineering
# ---------------------------------------------------------------------------

def _load_history() -> pd.DataFrame:
    mtime = os.path.getmtime(_EXCEL_PATH)
    if "inflow_history" not in _cache or _cache.get("inflow_history_mtime") != mtime:
        df = _load_excel()
        df = df.sort_values("Date").reset_index(drop=True)
        _cache["inflow_history"]       = df
        _cache["inflow_history_mtime"] = mtime
    return _cache["inflow_history"]


def _load_excel() -> pd.DataFrame:
    df = pd.read_excel(_EXCEL_PATH, sheet_name=_SHEET)
    df.columns = [c.strip() for c in df.columns]
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def _save_excel(df: pd.DataFrame) -> None:
    with pd.ExcelWriter(_EXCEL_PATH, engine="openpyxl", mode="w") as writer:
        df.to_excel(writer, sheet_name=_SHEET, index=False)


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["Date", "inflow_m3h"]].copy()

    # Seasonal features (cyclical encoding)
    out["month"]     = out["Date"].dt.month
    out["doy"]       = out["Date"].dt.dayofyear
    out["month_sin"] = np.sin(2 * np.pi * (out["month"] - 1) / 12)
    out["month_cos"] = np.cos(2 * np.pi * (out["month"] - 1) / 12)
    out["doy_sin"]   = np.sin(2 * np.pi * out["doy"] / 365)
    out["doy_cos"]   = np.cos(2 * np.pi * out["doy"] / 365)

    # Lag features (no future leakage — shifted ≥1 day)
    q = out["inflow_m3h"]
    out["lag_1d"]         = q.shift(1)
    out["lag_7d"]         = q.shift(7)
    out["lag_30d"]        = q.shift(30)
    out["roll_mean_7d"]   = q.shift(1).rolling(7).mean()
    out["roll_mean_30d"]  = q.shift(1).rolling(30).mean()
    out["roll_std_7d"]    = q.shift(1).rolling(7).std()
    out["inflow_diff_1d"] = q.shift(1) - q.shift(2)   # trend signal

    return out


def _feature_cols() -> List[str]:
    return [
        "month_sin", "month_cos", "doy_sin", "doy_cos",
        "lag_1d", "lag_7d", "lag_30d",
        "roll_mean_7d", "roll_mean_30d", "roll_std_7d",
        "inflow_diff_1d",
    ]


# ---------------------------------------------------------------------------
# Fallback — monthly climatological mean from plant.yaml
# ---------------------------------------------------------------------------

def _climatology_fallback(hours: List[int], delivery_date: str,
                          res_cfg: ReservoirConfig) -> Dict[int, float]:
    month = int(delivery_date[5:7])
    mean  = res_cfg.inflow_for_month(month)
    return {h: mean for h in hours}
