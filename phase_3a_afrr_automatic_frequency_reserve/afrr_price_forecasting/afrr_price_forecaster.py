"""
afrr_price_forecaster.py — aFRR capacity (availability) cap-price forecast.

Two separate Ridge/LightGBM models, one per direction:
  cap_up : EUR/MW paid for holding upward reserve available
           rises with scarcity (high DA price hours, peak demand)
  cap_dn : EUR/MW paid for holding downward reserve available
           rises with surplus (low DA price, solar hours)

Training data: afrr_training_data_2019_2025.xlsx
    61,368 rows · H1-H24 · 2019-01-01 to 2025-12-31
    Source: synthetic proxy anchored to REN/eSIO aFRR clearing price structure.
    Real data available from ENTSO-E Transparency Platform and eSIO portal.

Cap ceiling: 250 EUR/MW (REN regulatory cap). Floor: 0 EUR/MW.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))

_PHASE1_FCST = os.path.join(_REPO, "phase_1_da_day_ahead_bidding",
                             "price_and_power_forecasting")
if _PHASE1_FCST not in sys.path:
    sys.path.insert(0, _PHASE1_FCST)

from ml_train_val_test_common import fit_ridge, fit_lgbm, mae as _mae, walk_forward_cv

_EXCEL_PATH  = os.path.join(_HERE, "afrr_training_data_2019_2025.xlsx")
_SHEET       = "AFRR_2019_2025"
_JSON_UP     = os.path.join(_HERE, "afrr_up_selected_model.json")
_JSON_DN     = os.path.join(_HERE, "afrr_dn_selected_model.json")
_WARMUP_ROWS = 200
_N_CV_FOLDS  = 4
_CAP_MAX     = 250.0
_cache: dict = {}


def forecast_afrr_cap_prices(hours: List[int], delivery_date: str,
                             cap_price_max: float
                             ) -> Tuple[Dict[int, float], Dict[int, float]]:
    """Return (cap_up, cap_dn) each {hour: EUR/MW} for delivery_date."""
    try:
        return _model_forecast(hours, delivery_date, cap_price_max)
    except Exception as exc:
        import warnings
        warnings.warn(
            f"[aFRR Forecaster] Model failed ({exc}); using flat fallback.",
            RuntimeWarning, stacklevel=2
        )
        return {h: 25.0 for h in hours}, {h: 12.0 for h in hours}


def _model_forecast(hours: List[int], delivery_date: str,
                    cap_price_max: float) -> Tuple[Dict[int, float], Dict[int, float]]:
    df        = _load_history()
    target_dt = pd.Timestamp(delivery_date)
    train_raw = df[df["Date"] <= target_dt - pd.Timedelta(days=1)].copy()
    if len(train_raw) < _WARMUP_ROWS:
        raise ValueError(f"Insufficient aFRR history ({len(train_raw)} rows)")

    feat_tr   = _build_features(train_raw).dropna()
    cache_key = f"afrr_{delivery_date}"

    if cache_key not in _cache:
        sel_up, sel_dn = _auto_select_models(feat_tr)
        fcols = _feature_cols()
        X     = feat_tr[fcols]

        model_up = (fit_lgbm(X, feat_tr["cap_up_EUR_MW"].values, fcols) if sel_up == "LightGBM"
                    else fit_ridge(X.values, feat_tr["cap_up_EUR_MW"].values) if sel_up == "Ridge"
                    else None)
        model_dn = (fit_lgbm(X, feat_tr["cap_dn_EUR_MW"].values, fcols) if sel_dn == "LightGBM"
                    else fit_ridge(X.values, feat_tr["cap_dn_EUR_MW"].values) if sel_dn == "Ridge"
                    else None)

        _cache[cache_key] = (model_up, model_dn, sel_up, sel_dn)

    model_up, model_dn, sel_up, sel_dn = _cache[cache_key]
    pred_rows = _build_pred_rows(delivery_date, hours, feat_tr)
    fcols     = _feature_cols()
    X_pred    = pred_rows[fcols]

    def _predict(model, sel):
        if model is None:
            return np.zeros(len(pred_rows))
        return model.predict(X_pred) if sel == "LightGBM" else model.predict(X_pred.values)

    preds_up = _predict(model_up, sel_up)
    preds_dn = _predict(model_dn, sel_dn)

    cap_up, cap_dn = {}, {}
    for i, h in enumerate(pred_rows["hour"].astype(int).tolist()):
        cap_up[h] = round(float(np.clip(preds_up[i], 0.0, cap_price_max)), 2)
        cap_dn[h] = round(float(np.clip(preds_dn[i], 0.0, cap_price_max)), 2)
    return cap_up, cap_dn


def _auto_select_models(feat_tr: pd.DataFrame) -> Tuple[str, str]:
    excel_last = feat_tr["Date"].max().date()
    fcols = _feature_cols()

    def _load_json(path):
        if os.path.exists(path):
            with open(path) as f:
                info = json.load(f)
            if pd.Timestamp(info.get("data_end_date", "2000-01-01")).date() >= excel_last:
                return info["selected"]
        return None

    sel_up = _load_json(_JSON_UP)
    sel_dn = _load_json(_JSON_DN)
    if sel_up and sel_dn:
        return sel_up, sel_dn

    X     = feat_tr[fcols]
    today = str(datetime.date.today())

    if not sel_up:
        y_up   = feat_tr["cap_up_EUR_MW"].values
        cv_up  = walk_forward_cv(X, y_up, np.full_like(y_up, y_up.mean()), fcols, _N_CV_FOLDS)
        sel_up = min(cv_up, key=cv_up.get)
        with open(_JSON_UP, "w") as f:
            json.dump({"selected": sel_up,
                       "cv_mae": {k: round(v, 4) for k, v in cv_up.items()},
                       "data_end_date": str(excel_last), "updated_on": today}, f, indent=2)
        print(f"\n[aFRR Up Forecaster] Selected => {sel_up}")
        for name in ["Naive", "Ridge", "LightGBM"]:
            mark = " <--" if name == sel_up else ""
            print(f"  {name:<12} MAE {cv_up.get(name, float('inf')):.4f} EUR/MW{mark}")

    if not sel_dn:
        y_dn   = feat_tr["cap_dn_EUR_MW"].values
        cv_dn  = walk_forward_cv(X, y_dn, np.full_like(y_dn, y_dn.mean()), fcols, _N_CV_FOLDS)
        sel_dn = min(cv_dn, key=cv_dn.get)
        with open(_JSON_DN, "w") as f:
            json.dump({"selected": sel_dn,
                       "cv_mae": {k: round(v, 4) for k, v in cv_dn.items()},
                       "data_end_date": str(excel_last), "updated_on": today}, f, indent=2)
        print(f"\n[aFRR Dn Forecaster] Selected => {sel_dn}")
        for name in ["Naive", "Ridge", "LightGBM"]:
            mark = " <--" if name == sel_dn else ""
            print(f"  {name:<12} MAE {cv_dn.get(name, float('inf')):.4f} EUR/MW{mark}")

    return sel_up, sel_dn


def _load_history() -> pd.DataFrame:
    mtime = os.path.getmtime(_EXCEL_PATH)
    if "afrr_hist" not in _cache or _cache.get("afrr_hist_mtime") != mtime:
        df = pd.read_excel(_EXCEL_PATH, sheet_name=_SHEET)
        df.columns = [c.strip() for c in df.columns]
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values(["Date", "Hour"]).reset_index(drop=True)
        _cache["afrr_hist"]       = df
        _cache["afrr_hist_mtime"] = mtime
    return _cache["afrr_hist"]


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["Date", "Hour", "price_DA_PT_EUR_MWh",
              "cap_up_EUR_MW", "cap_dn_EUR_MW"]].copy()
    out = out.rename(columns={"Hour": "hour", "price_DA_PT_EUR_MWh": "da_price"})

    out["hour_sin"]   = np.sin(2 * np.pi * (out["hour"] - 1) / 24)
    out["hour_cos"]   = np.cos(2 * np.pi * (out["hour"] - 1) / 24)
    dow               = out["Date"].dt.dayofweek
    month             = out["Date"].dt.month
    out["dow_sin"]    = np.sin(2 * np.pi * dow / 7)
    out["dow_cos"]    = np.cos(2 * np.pi * dow / 7)
    out["month_sin"]  = np.sin(2 * np.pi * (month - 1) / 12)
    out["month_cos"]  = np.cos(2 * np.pi * (month - 1) / 12)
    out["is_weekend"] = (dow >= 5).astype(int)

    out = out.sort_values(["Date", "hour"]).reset_index(drop=True)
    grp = out.groupby("Date", sort=False)
    out["da_roll_mean_24h"] = grp["da_price"].transform(
        lambda x: x.rolling(min(24, len(x)), min_periods=1).mean())
    out["da_roll_std_24h"]  = grp["da_price"].transform(
        lambda x: x.rolling(min(24, len(x)), min_periods=2).std().fillna(0.0))

    lag_df = out[["Date", "hour", "cap_up_EUR_MW", "cap_dn_EUR_MW"]].copy()
    lag_df["Date"] = lag_df["Date"] + pd.Timedelta(days=7)
    lag_df = lag_df.rename(columns={"cap_up_EUR_MW": "cap_up_lag_168h",
                                     "cap_dn_EUR_MW": "cap_dn_lag_168h"})
    out = out.merge(lag_df, on=["Date", "hour"], how="left")
    return out


def _build_pred_rows(delivery_date: str, hours: List[int],
                     feat_tr: pd.DataFrame) -> pd.DataFrame:
    target_dt  = pd.Timestamp(delivery_date)
    dow, month = target_dt.dayofweek, target_dt.month
    week_ago   = (target_dt - pd.Timedelta(days=7)).date()
    recent     = feat_tr[feat_tr["Date"].dt.date == week_ago].groupby("hour")[
        ["cap_up_EUR_MW", "cap_dn_EUR_MW", "da_price"]].mean()

    rows = []
    for h in sorted(hours):
        da_p    = float(recent.loc[h, "da_price"]) if h in recent.index else 65.0
        cap_up_ = float(recent.loc[h, "cap_up_EUR_MW"]) if h in recent.index else 25.0
        cap_dn_ = float(recent.loc[h, "cap_dn_EUR_MW"]) if h in recent.index else 12.0
        rows.append({
            "hour"             : h,
            "da_price"         : da_p,
            "hour_sin"         : np.sin(2 * np.pi * (h - 1) / 24),
            "hour_cos"         : np.cos(2 * np.pi * (h - 1) / 24),
            "dow_sin"          : np.sin(2 * np.pi * dow / 7),
            "dow_cos"          : np.cos(2 * np.pi * dow / 7),
            "month_sin"        : np.sin(2 * np.pi * (month - 1) / 12),
            "month_cos"        : np.cos(2 * np.pi * (month - 1) / 12),
            "is_weekend"       : int(dow >= 5),
            "da_roll_mean_24h" : da_p,
            "da_roll_std_24h"  : 0.0,
            "cap_up_lag_168h"  : cap_up_,
            "cap_dn_lag_168h"  : cap_dn_,
        })
    return pd.DataFrame(rows)


def _feature_cols() -> List[str]:
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
