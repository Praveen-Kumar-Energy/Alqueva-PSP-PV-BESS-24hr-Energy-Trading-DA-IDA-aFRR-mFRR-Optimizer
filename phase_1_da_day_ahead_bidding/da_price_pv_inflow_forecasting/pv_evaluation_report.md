# PV Forecaster — Train / Validation / Test Report

_Generated: 2026-06-23_

## Methodology

Chronological train / validation / test — no shuffling, no future leakage.
Walk-forward CV (4 folds) on development set selects the model.
Final 12-month held-out test set (never seen during selection) gives unbiased accuracy below.
Serving forecaster (pv_power_forecaster.py) retrains on all history daily.

Skill score = 1 − MAE_model / MAE_naive; positive = beats naive 24h persistence.

---
### PV — Global Horizontal Irradiance

- Development  : 87,503 rows (2015-01-08 → 2024-12-31)
- Test (held out): 8,761 rows (2024-12-31 → 2025-12-31)
- Selected model : **LightGBM**

**Walk-forward CV (validation MAE — model selection):**

| Model | MAE (W/m2) |
|-------|-----------|
| Naive persistence | 11.10 |
| Ridge regression  | 11.56 |
| LightGBM          | 5.66 |

**Held-out TEST (unbiased):**

| Metric | Value |
|--------|-------|
| MAE        | 50.65 W/m2 |
| RMSE       | 89.63 W/m2 |
| Bias (ME)  | -12.79 W/m2 |
| Skill vs naive | -30.9% |

**Feature Importance (LightGBM — top 10):**

| Feature | Importance |
|---------|-----------|
| doy | 5469 |
| lag_kt_24h | 5221 |
| roll_std_24h_GHI | 4128 |
| roll_mean_24h_GHI | 3948 |
| clearsky_ghi | 2485 |
| lag_24h_GHI | 2363 |
| lag_48h_GHI | 2085 |
| hour_sin | 2050 |
| lag_168h_GHI | 1763 |
| hour_cos | 783 |

---

### PV — Ambient Temperature

- Development  : 87,503 rows (2015-01-08 → 2024-12-31)
- Test (held out): 8,761 rows (2024-12-31 → 2025-12-31)
- Selected model : **Naive**

**Walk-forward CV (validation MAE — model selection):**

| Model | MAE (degC) |
|-------|-----------|
| Naive persistence | 0.09 |
| Ridge regression  | 0.22 |
| LightGBM          | 0.20 |

**Held-out TEST (unbiased):**

| Metric | Value |
|--------|-------|
| MAE        | 1.74 degC |
| RMSE       | 2.27 degC |
| Bias (ME)  | +0.01 degC |
| Skill vs naive | +0.0% |
