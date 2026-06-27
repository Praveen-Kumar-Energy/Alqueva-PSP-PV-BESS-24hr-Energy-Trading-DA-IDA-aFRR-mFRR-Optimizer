"""
xbid_price_forecaster.py — XBID continuous intraday price forecast.

Spread model trained on synthetic XBID mid-price proxy data (H1-H24, 2024-2025).
Real XBID order-book data requires a commercial EPEX SPOT subscription; the proxy
uses IDA3 clearing price + microstructure noise as the training target — a standard
approach for desks without a live order-book feed.

Training data: xbid_training_data_2024_2025.xlsx
    13,608 rows · H1-H24 · 2024-06-13 to 2025-12-31
    Source: synthetic XBID mid-price proxy (IDA3 + OU spread noise, std ~14 EUR/MWh)

Spread std wider than IDA3 (~11 EUR/MWh): XBID is closer to delivery, more
microstructure noise, no single clearing — consistent with market microstructure.

Floor: -500 EUR/MWh (OMIE regulatory minimum).
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from typing import Dict, List

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))

_PHASE1_FCST = os.path.join(_REPO, "phase_1_da_day_ahead_bidding",
                             "price_and_power_forecasting")
if _PHASE1_FCST not in sys.path:
    sys.path.insert(0, _PHASE1_FCST)

from ml_train_val_test_common import fit_ridge, fit_lgbm, mae as _mae, walk_forward_cv

_EXCEL_PATH  = os.path.join(_HERE, "xbid_training_data_2024_2025.xlsx")
_SHEET       = "XBID_2024_2025"
_JSON_PATH   = os.path.join(_HERE, "xbid_selected_model.json")
_WARMUP_ROWS = 100
_N_CV_FOLDS  = 4
_PRICE_FLOOR = -500.0
_cache: dict = {}


def forecast_xbid_prices(hours: List[int], delivery_date: str,
                         da_prices: Dict[int, float]) -> Dict[int, float]:
    """Return {hour: EUR/MWh} XBID price forecast for delivery_date."""
    try:
        return _model_forecast(hours, delivery_date, da_prices)
    except Exception as exc:
        import warnings
        warnings.warn(
            f"[XBID Forecaster] Model failed ({exc}); using DA prices as fallback.",
            RuntimeWarning, stacklevel=2
        )
        return {h: da_prices.get(h, 55.0) for h in hours}


def _model_forecast(hours: List[int], delivery_date: str,
                    da_prices: Dict[int, float]) -> Dict[int, float]:
    df        = _load_history()
    target_dt = pd.Timestamp(delivery_date)
    train_raw = df[df["Date"] <= target_dt - pd.Timedelta(days=1)].copy()
    if len(train_raw) < _WARMUP_ROWS:
        raise ValueError(f"Insufficient XBID history ({len(train_raw)} rows)")

    feat_tr   = _build_features(train_raw).dropna()
    cache_key = f"xbid_{delivery_date}"

    if cache_key not in _cache:
        selected = _auto_select_model(feat_tr)
        fcols    = _feature_cols()
        X, y     = feat_tr[fcols], feat_tr["spread_EUR_MWh"].values
        model    = (fit_lgbm(X, y, fcols) if selected == "LightGBM"
                    else fit_ridge(X.values, y) if selected == "Ridge"
                    else None)
        _cache[cache_key]                  = model
        _cache[f"sel_xbid_{delivery_date}"] = selected

    model    = _cache[cache_key]
    selected = _cache[f"sel_xbid_{delivery_date}"]
    pred_rows = _build_pred_rows(delivery_date, hours, da_prices, feat_tr)

    if model is None:
        spreads = np.zeros(len(pred_rows))
    elif selected == "Ridge":
        spreads = model.predict(pred_rows[_feature_cols()].values)
    else:
        spreads = model.predict(pred_rows[_feature_cols()])

    return {
        int(h): round(float(np.clip(da_prices.get(int(h), 55.0) + spreads[i],
                                    _PRICE_FLOOR, 3_000.0)), 2)
        for i, h in enumerate(pred_rows["hour"].astype(int).tolist())
    }


def _auto_select_model(feat_tr: pd.DataFrame) -> str:
    excel_last_date = feat_tr["Date"].max().date()
    if os.path.exists(_JSON_PATH):
        with open(_JSON_PATH) as f:
            info = json.load(f)
        if pd.Timestamp(info.get("data_end_date", "2000-01-01")).date() >= excel_last_date:
            return info["selected"]

    fcols    = _feature_cols()
    y        = feat_tr["spread_EUR_MWh"].values
    cv_mae   = walk_forward_cv(feat_tr[fcols], y, np.zeros_like(y), fcols, _N_CV_FOLDS)
    selected = min(cv_mae, key=cv_mae.get)

    with open(_JSON_PATH, "w") as f:
        json.dump({"selected": selected,
                   "cv_mae": {k: round(v, 4) for k, v in cv_mae.items()},
                   "data_end_date": str(excel_last_date),
                   "updated_on": str(datetime.date.today())}, f, indent=2)
    print(f"\n[XBID Forecaster] Selected => {selected}")
    for name in ["Naive", "Ridge", "LightGBM"]:
        mark = " <--" if name == selected else ""
        print(f"  {name:<12} MAE {cv_mae.get(name, float('inf')):.4f} EUR/MWh{mark}")
    return selected


def _load_history() -> pd.DataFrame:
    mtime = os.path.getmtime(_EXCEL_PATH)
    if "xbid_hist" not in _cache or _cache.get("xbid_hist_mtime") != mtime:
        df = pd.read_excel(_EXCEL_PATH, sheet_name=_SHEET)
        df.columns = [c.strip() for c in df.columns]
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values(["Date", "Hour"]).reset_index(drop=True)
        _cache["xbid_hist"]       = df
        _cache["xbid_hist_mtime"] = mtime
    return _cache["xbid_hist"]


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["Date", "Hour", "price_DA_PT_EUR_MWh", "spread_EUR_MWh"]].copy()
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

    lag_df = out[["Date", "hour", "spread_EUR_MWh"]].copy()
    lag_df["Date"] = lag_df["Date"] + pd.Timedelta(days=7)
    lag_df = lag_df.rename(columns={"spread_EUR_MWh": "da_lag_168h_spread"})
    out = out.merge(lag_df, on=["Date", "hour"], how="left")
    out["spread_lag_h1"] = grp["spread_EUR_MWh"].transform(lambda x: x.shift(1))
    return out


def _build_pred_rows(delivery_date: str, hours: List[int],
                     da_prices: Dict[int, float],
                     feat_tr: pd.DataFrame) -> pd.DataFrame:
    target_dt  = pd.Timestamp(delivery_date)
    dow, month = target_dt.dayofweek, target_dt.month
    week_ago   = (target_dt - pd.Timedelta(days=7)).date()
    recent     = (feat_tr[feat_tr["Date"].dt.date == week_ago]
                  .groupby("hour")["spread_EUR_MWh"].mean())
    rows, prev = [], 0.0
    for h in sorted(hours):
        da_p = float(da_prices.get(h, 55.0))
        rows.append({
            "hour"              : h,
            "da_price"          : da_p,
            "hour_sin"          : np.sin(2 * np.pi * (h - 1) / 24),
            "hour_cos"          : np.cos(2 * np.pi * (h - 1) / 24),
            "dow_sin"           : np.sin(2 * np.pi * dow / 7),
            "dow_cos"           : np.cos(2 * np.pi * dow / 7),
            "month_sin"         : np.sin(2 * np.pi * (month - 1) / 12),
            "month_cos"         : np.cos(2 * np.pi * (month - 1) / 12),
            "is_weekend"        : int(dow >= 5),
            "da_roll_mean_24h"  : float(np.mean(list(da_prices.values()))),
            "da_roll_std_24h"   : float(np.std(list(da_prices.values()))),
            "da_lag_168h_spread": float(recent.get(h, 0.0)),
            "spread_lag_h1"     : prev,
        })
        prev = 0.0
    return pd.DataFrame(rows)


def _feature_cols() -> List[str]:
    return [
        "hour_sin", "hour_cos",
        "dow_sin", "dow_cos",
        "month_sin", "month_cos",
        "is_weekend",
        "da_price",
        "da_roll_mean_24h", "da_roll_std_24h",
        "da_lag_168h_spread",
        "spread_lag_h1",
    ]
