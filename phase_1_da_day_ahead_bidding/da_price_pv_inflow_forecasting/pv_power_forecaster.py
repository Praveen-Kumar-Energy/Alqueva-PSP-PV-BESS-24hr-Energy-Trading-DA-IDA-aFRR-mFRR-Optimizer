"""
pv_power_forecaster.py — hourly PV availability for the Alqueva floating array.

Methodology:
    1. Load pv_training_data_2015_2025.xlsx (ERA5 reanalysis: GHI W/m² + T_amb °C)
         GHI   <- ERA5 surface_solar_radiation_downwards
         T_amb <- ERA5 2m_temperature
    2. Gap fill: any missing dates between last Excel row and yesterday are filled
       with synthetic clear-sky GHI (Bird model × mean cloud factor) and seasonal
       T_amb — no external API needed, pure physics + climatology
    3. Compute clear-sky GHI from solar geometry (Alqueva 38.20°N, 7.49°W)
    4. Build lag + clear-sky features for GHI and T_amb separately
    5. Walk-forward CV (4 folds) comparing Naive / Ridge / LightGBM per target
    6. Forecast GHI and T_amb for each hour of delivery_date
    7. Convert T_amb → T_cell via NOCT model (IEC 61215, NOCT = 45°C floating PV)
       T_cell = T_amb + (NOCT − 20) / 800 × GHI
    8. Feed (GHI, T_cell) into pv_production_model.production_mw() — same physics
       used by the MILP optimiser and constraint checker

Returned shape: {hour: MW available} for hours 1..24.
Fallback: if data unavailable, returns deterministic clear-sky profile so the
pipeline never crashes.

Reference: Diagne et al. (2013) "Review of solar irradiance forecasting methods
and a proposition for small-scale insular grids" — lag + clear-sky features
standard for short-term GHI forecasting.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import random
import sys
import warnings
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# Ensure this folder is on sys.path so ml_train_val_test_common resolves
# whether imported from run_da.py / run_production.py or run directly
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from ml_train_val_test_common import fit_ridge, fit_lgbm, mae as _mae, walk_forward_cv

from common_layer.configuration.plant_config import PVConfig
from common_layer.physical_plant_models import PVModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_EXCEL_PATH   = os.path.join(_HERE, "pv_training_data_2015_2025.xlsx")
_JSON_PATH    = os.path.join(_HERE, "pv_selected_model.json")
_SHEET        = "PV_Weather_2015_2026"
_LAT_DEG      = 38.20    # Alqueva latitude (°N)
_LON_DEG      = -7.49    # Alqueva longitude (°E, negative = west)
_NOCT_C       = 45.0     # Nominal Operating Cell Temperature (floating PV, IEC 61215)
_KT_MEAN      = 0.55     # Mean clearness index for Alqueva (cloud correction)
_WARMUP_HOURS = 336      # 2 weeks minimum history before first CV fold
_N_CV_FOLDS   = 4
_cache: dict  = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def forecast_pv_available(hours: List[int], delivery_date: str,
                          pv_cfg: PVConfig) -> Dict[int, float]:
    """Return {hour: MW available} for the 24 hours of delivery_date.

    Fills any Excel gap up to yesterday with synthetic weather, then trains
    GHI and T_amb models on full history and predicts delivery_date.
    Falls back to clear-sky synthetic profile if anything fails.
    """
    try:
        return _model_forecast(hours, delivery_date, pv_cfg)
    except Exception as exc:
        warnings.warn(
            f"[PV Forecaster] Model failed ({exc}); using synthetic fallback. "
            f"Check {_EXCEL_PATH}.",
            RuntimeWarning, stacklevel=2
        )
        return _synthetic_fallback(hours, delivery_date, pv_cfg)


# ---------------------------------------------------------------------------
# Gap fill — called before training
# ---------------------------------------------------------------------------

def _fill_gaps(delivery_date: str) -> None:
    """Fill pv_training_data Excel with synthetic weather up to yesterday.

    ERA5 has a ~5-day publication lag — real data not available for recent
    dates. Gap is filled with clear-sky GHI × mean cloud factor and seasonal
    T_amb. Idempotent: already-filled dates are skipped.
    """
    target_dt = pd.Timestamp(delivery_date)
    yesterday = target_dt - pd.Timedelta(days=1)

    df       = _load_excel()
    last_dt  = df["Date"].max() if not df.empty else pd.Timestamp("2014-12-31")

    if last_dt >= yesterday:
        return  # already current

    missing = pd.date_range(start=last_dt + pd.Timedelta(days=1),
                            end=yesterday, freq="D")

    try:
        from common_layer.utilities.logging_utils import get_logger
        log = get_logger(__name__)
        log.info(f"[PV] Gap fill: {len(missing)} date(s) "
                 f"{missing[0].date()} → {missing[-1].date()} (synthetic)")
    except Exception:
        pass

    new_rows = []
    for dt in missing:
        month = dt.month
        t_amb_mean = _seasonal_tamb(month)
        for h in range(1, 25):
            cs  = _clearsky_ghi(pd.Timestamp(dt) + pd.Timedelta(hours=h - 1))
            ghi = round(cs * _KT_MEAN, 1)
            new_rows.append({
                "Date"  : dt,
                "Hour"  : h,
                "GHI"   : ghi,
                "T_amb" : round(t_amb_mean + 5.0 * math.sin(math.pi * (h - 6) / 12), 2),
                "source": "SYNTHETIC",
            })

    updated = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    updated = updated.sort_values(["Date", "Hour"]).reset_index(drop=True)
    _save_excel(updated)

    # Invalidate cache so next _load_history() sees new rows
    _cache.pop("pv_history", None)


def _seasonal_tamb(month: int) -> float:
    """Mean daily T_amb (°C) for Alqueva by month — Alentejo climatology."""
    means = {1: 9.5, 2: 11.0, 3: 13.5, 4: 15.5, 5: 19.0, 6: 23.5,
             7: 26.5, 8: 26.5, 9: 23.0, 10: 18.0, 11: 13.0, 12: 10.0}
    return means.get(month, 18.0)


# ---------------------------------------------------------------------------
# Core forecasting pipeline
# ---------------------------------------------------------------------------

def _model_forecast(hours: List[int], delivery_date: str,
                    pv_cfg: PVConfig) -> Dict[int, float]:
    # Step 1 — fill Excel gap up to yesterday
    _fill_gaps(delivery_date)

    df        = _load_history()
    target_dt = pd.Timestamp(delivery_date)
    cutoff    = target_dt - pd.Timedelta(hours=1)
    year      = target_dt.year

    # Drop any pre-existing delivery-date (or future) rows so the appended
    # placeholder is the only set of delivery-date rows. A prior _fill_gaps for a
    # later date can otherwise leave duplicate rows that corrupt the lag /
    # rolling features and force the synthetic fallback on every run.
    df = df[df["datetime"] < target_dt].copy()

    history = df[df["datetime"] <= cutoff]
    if len(history) < _WARMUP_HOURS:
        raise ValueError(f"Insufficient history ({len(history)} rows)")

    # Append 24 placeholder rows for delivery_date.
    # GHI/T_amb initialised to Naive (yesterday's values) so rolling features
    # stay non-NaN for H2..H24. Actual ML prediction overwrites these.
    yesterday = (target_dt - pd.Timedelta(days=1)).date()
    yest_df   = df[df["datetime"].dt.date == yesterday].set_index("Hour")
    naive_ghi  = [float(yest_df["GHI"].get(h,  yest_df["GHI"].mean()))  for h in range(1, 25)]
    naive_tamb = [float(yest_df["T_amb"].get(h, yest_df["T_amb"].mean())) for h in range(1, 25)]
    placeholder = pd.DataFrame({
        "datetime": [target_dt + pd.Timedelta(hours=h - 1) for h in range(1, 25)],
        "Hour"    : list(range(1, 25)),
        "GHI"     : naive_ghi,
        "T_amb"   : naive_tamb,
    })
    df_ext   = pd.concat([df, placeholder], ignore_index=True).sort_values("datetime")
    full     = _build_features(df_ext)
    train_df = full[full["datetime"] <= cutoff].dropna()
    pred_df  = full[full["datetime"].dt.date == target_dt.date()].copy()

    if pred_df.empty:
        raise ValueError(f"No feature rows for {delivery_date}")

    pv_model = PVModel(pv_cfg, year=year)

    # Step 2 — train GHI and T_amb models separately (cached per delivery_date)
    for target_col, suffix in [("GHI", "ghi"), ("T_amb", "tamb")]:
        cache_key = f"pv_{suffix}_{delivery_date}"
        if cache_key not in _cache:
            fcols    = _feature_cols(target_col)
            feat_df  = train_df[fcols]
            y        = train_df[target_col].values
            selected = _auto_select_pv_model(train_df, target_col, fcols)

            if selected == "LightGBM":
                model = fit_lgbm(feat_df, y, fcols)
            elif selected == "Ridge":
                model = fit_ridge(feat_df.values, y)
            else:
                model = None  # Naive

            _cache[cache_key]                           = model
            _cache[f"pv_{suffix}_sel_{delivery_date}"] = selected

    # Step 3 — predict GHI and T_amb for delivery_date
    ghi_pred  = _predict(pred_df, "ghi",  delivery_date, clip_min=0.0)
    tamb_pred = _predict(pred_df, "tamb", delivery_date, clip_min=None)

    # Step 4 — physics: T_cell → PV MW (via common_layer PVModel)
    out: Dict[int, float] = {}
    for h in hours:
        ghi    = ghi_pred.get(h, 0.0)
        t_amb  = tamb_pred.get(h, 15.0)
        t_cell = t_amb + ((_NOCT_C - 20.0) / 800.0) * ghi   # IEC 61215
        out[h] = round(pv_model.production_mw(ghi, t_cell), 4)

    return out


def _predict(pred_df: pd.DataFrame, suffix: str,
             delivery_date: str, clip_min: Optional[float]) -> Dict[int, float]:
    cache_key  = f"pv_{suffix}_{delivery_date}"
    sel_key    = f"pv_{suffix}_sel_{delivery_date}"
    model      = _cache[cache_key]
    selected   = _cache.get(sel_key, "LightGBM")
    target_col = "GHI" if suffix == "ghi" else "T_amb"
    lag_col    = f"lag_24h_{target_col}"
    fcols      = _feature_cols(target_col)
    X          = pred_df[fcols]

    if model is None:
        preds = pred_df[lag_col].values
    elif selected == "Ridge":
        preds = model.predict(X.values)
    else:
        preds = model.predict(X)

    if clip_min is not None:
        preds = np.clip(preds, clip_min, None)

    lookup = dict(zip(pred_df["hour"].astype(int), preds))
    return {h: float(v) for h, v in lookup.items()}


# ---------------------------------------------------------------------------
# Auto model selection — CV only when new data arrives
# ---------------------------------------------------------------------------

def _auto_select_pv_model(train_df: pd.DataFrame, target_col: str,
                          fcols: List[str]) -> str:
    """Return selected model for target_col. Re-runs CV only when Excel has new data.

    Reads pv_selected_model.json (one entry per target: GHI / T_amb).
    If data_end_date matches current Excel end → return cached selection.
    Otherwise → re-run walk-forward CV → update json.
    """
    excel_last_date = train_df["datetime"].max().date()

    # Check existing json for this target
    if os.path.exists(_JSON_PATH):
        with open(_JSON_PATH, "r") as f:
            info = json.load(f)
        entry = info.get(target_col, {})
        saved_end = pd.Timestamp(entry.get("data_end_date", "2000-01-01")).date()
        if saved_end >= excel_last_date:
            return entry["selected"]   # still current — no CV needed
    else:
        info = {}

    # New data or first run → walk-forward CV
    lag_col = f"lag_24h_{target_col}"
    feat_df = train_df[fcols]
    y       = train_df[target_col].values
    lag24   = train_df[lag_col].values

    cv_mae   = walk_forward_cv(feat_df, y, lag24, fcols, _N_CV_FOLDS)
    selected = min(cv_mae, key=cv_mae.get)

    info[target_col] = {
        "selected"     : selected,
        "cv_mae"       : {k: round(v, 4) for k, v in cv_mae.items()},
        "data_end_date": str(excel_last_date),
        "updated_on"   : str(datetime.date.today()),
    }
    with open(_JSON_PATH, "w") as f:
        json.dump(info, f, indent=2)

    unit = "W/m2" if target_col == "GHI" else "degC"
    print(f"\n[PV Forecaster] {target_col} model selection updated → {selected}")
    print(f"  Data up to : {excel_last_date}")
    for name in ["Naive", "Ridge", "LightGBM"]:
        marker = " <-- selected" if name == selected else ""
        print(f"  {name:<22} MAE {cv_mae.get(name, float('inf')):.2f} {unit}{marker}")
    print()

    return selected


# ---------------------------------------------------------------------------
# Data loading and feature engineering
# ---------------------------------------------------------------------------

def _load_history() -> pd.DataFrame:
    mtime = os.path.getmtime(_EXCEL_PATH)
    if "pv_history" not in _cache or _cache.get("pv_history_mtime") != mtime:
        df = _load_excel()
        df["datetime"] = df["Date"] + pd.to_timedelta(df["Hour"] - 1, unit="h")
        df = df.sort_values("datetime").reset_index(drop=True)
        _cache["pv_history"]       = df
        _cache["pv_history_mtime"] = mtime
    return _cache["pv_history"]


def _load_excel() -> pd.DataFrame:
    df = pd.read_excel(_EXCEL_PATH, sheet_name=_SHEET)
    df.columns = [c.strip() for c in df.columns]
    # Normalise column names regardless of unit suffix in header
    hour_col = next(c for c in df.columns if "hour" in c.lower())
    ghi_col  = next(c for c in df.columns if c.upper().startswith("GHI"))
    tamb_col = next(c for c in df.columns if c.upper().startswith("T_AMB"))
    df = df.rename(columns={hour_col: "Hour", ghi_col: "GHI", tamb_col: "T_amb"})
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def _save_excel(df: pd.DataFrame) -> None:
    with pd.ExcelWriter(_EXCEL_PATH, engine="openpyxl", mode="w") as writer:
        df.to_excel(writer, sheet_name=_SHEET, index=False)


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["datetime", "Hour", "GHI", "T_amb"]].copy()
    out = out.rename(columns={"Hour": "hour"})

    # Calendar features (cyclical encoding)
    out["hour_sin"]  = np.sin(2 * np.pi * (out["hour"] - 1) / 24)
    out["hour_cos"]  = np.cos(2 * np.pi * (out["hour"] - 1) / 24)
    out["dow"]       = out["datetime"].dt.dayofweek
    out["month"]     = out["datetime"].dt.month
    out["is_weekend"]= (out["dow"] >= 5).astype(int)
    out["month_sin"] = np.sin(2 * np.pi * (out["month"] - 1) / 12)
    out["month_cos"] = np.cos(2 * np.pi * (out["month"] - 1) / 12)
    out["doy"]       = out["datetime"].dt.dayofyear

    # Clear-sky GHI — physical upper bound (Bird simplified model)
    out["clearsky_ghi"] = out["datetime"].apply(_clearsky_ghi)

    # Clearness index kt = actual / clearsky (cloud correction signal)
    out["kt"] = np.where(out["clearsky_ghi"] > 10,
                         out["GHI"] / out["clearsky_ghi"], 0.0)

    # GHI lag features (no future leakage — shifted ≥24h)
    ghi = out["GHI"]
    out["lag_24h_GHI"]      = ghi.shift(24)
    out["lag_48h_GHI"]      = ghi.shift(48)
    out["lag_168h_GHI"]     = ghi.shift(168)
    out["roll_mean_24h_GHI"]= ghi.shift(1).rolling(24).mean()
    out["roll_std_24h_GHI"] = ghi.shift(1).rolling(24).std()
    out["lag_kt_24h"]       = out["kt"].shift(24)   # yesterday's cloud ratio

    # T_amb lag features
    t = out["T_amb"]
    out["lag_24h_T_amb"]      = t.shift(24)
    out["lag_48h_T_amb"]      = t.shift(48)
    out["lag_168h_T_amb"]     = t.shift(168)
    out["roll_mean_24h_T_amb"]= t.shift(1).rolling(24).mean()
    out["roll_std_24h_T_amb"] = t.shift(1).rolling(24).std()

    return out


def _feature_cols(target_col: str) -> List[str]:
    common = [
        "hour_sin", "hour_cos", "month_sin", "month_cos",
        "doy", "is_weekend", "clearsky_ghi",
    ]
    if target_col == "GHI":
        return common + [
            "lag_24h_GHI", "lag_48h_GHI", "lag_168h_GHI",
            "roll_mean_24h_GHI", "roll_std_24h_GHI", "lag_kt_24h",
        ]
    else:  # T_amb
        return common + [
            "lag_24h_T_amb", "lag_48h_T_amb", "lag_168h_T_amb",
            "roll_mean_24h_T_amb", "roll_std_24h_T_amb",
        ]


# ---------------------------------------------------------------------------
# Clear-sky GHI — Bird simplified model (Alqueva 38.20°N, 7.49°W)
# ---------------------------------------------------------------------------

def _clearsky_ghi(dt: pd.Timestamp) -> float:
    """Theoretical clear-sky GHI in W/m² for Alqueva at the given UTC hour."""
    lat  = math.radians(_LAT_DEG)
    doy  = dt.timetuple().tm_yday
    hour = dt.hour

    # Solar declination (Spencer 1971)
    B   = 2 * math.pi * (doy - 1) / 365
    dec = (0.006918 - 0.399912 * math.cos(B)  + 0.070257 * math.sin(B)
                     - 0.006758 * math.cos(2*B)+ 0.000907 * math.sin(2*B)
                     - 0.002697 * math.cos(3*B)+ 0.001480 * math.sin(3*B))

    # Equation of time
    eot = (0.000075 + 0.001868*math.cos(B) - 0.032077*math.sin(B)
                    - 0.014615*math.cos(2*B) - 0.04089 *math.sin(2*B))

    # Solar time and hour angle
    solar_time = hour + (4 * (_LON_DEG - 0) + math.degrees(eot) * 60) / 60
    hour_angle = math.radians(15 * (solar_time - 12))

    # Solar elevation
    sin_elev = (math.sin(lat) * math.sin(dec)
                + math.cos(lat) * math.cos(dec) * math.cos(hour_angle))
    sin_elev = max(0.0, sin_elev)
    if sin_elev < 0.01:
        return 0.0

    # Air mass (Kasten & Young 1989)
    elev_deg = math.degrees(math.asin(sin_elev))
    am = min(1.0 / (sin_elev + 0.50572 * (elev_deg + 6.07995) ** -1.6364), 38.0)

    # Bird simplified transmittance (rural Portugal aerosols)
    tau_r = math.exp(-0.0903 * am**0.84 * (1 + am - am**1.01))
    tau_a = 0.935 ** am
    tau_w = 0.960 ** am
    tau_o = 0.997 ** am

    I0  = 1367 * (1 + 0.033 * math.cos(2 * math.pi * doy / 365))
    Ib  = 0.9662 * I0 * sin_elev * tau_r * tau_a * tau_w * tau_o
    Id  = 0.2710 * I0 * sin_elev - 0.2939 * Ib
    return round(min(max(0.0, Ib + max(0.0, Id)), 1200.0), 1)


# ---------------------------------------------------------------------------
# Synthetic fallback (pipeline-safe — never crashes)
# ---------------------------------------------------------------------------

def _synthetic_fallback(hours: List[int], delivery_date: str,
                        pv_cfg: PVConfig) -> Dict[int, float]:
    year  = int(delivery_date[:4])
    month = int(delivery_date[5:7])
    model = PVModel(pv_cfg, year=year)
    rng   = random.Random(f"pv-{delivery_date}")

    summer          = month in (5, 6, 7, 8)
    sunrise, sunset = (6, 21) if summer else (8, 18)
    peak_irr        = 950.0 if summer else 700.0
    cloud           = rng.uniform(0.75, 1.0)
    amb_peak_c      = 30.0  if summer else 18.0

    out: Dict[int, float] = {}
    for h in hours:
        if sunrise <= h <= sunset:
            frac   = math.sin((h - sunrise) / (sunset - sunrise) * math.pi)
            irr    = peak_irr * max(0.0, frac) * cloud
            t_amb  = amb_peak_c * max(0.3, frac)
            t_cell = t_amb + ((_NOCT_C - 20.0) / 800.0) * irr
            out[h] = round(model.production_mw(irr, t_cell), 4)
        else:
            out[h] = 0.0
    return out
