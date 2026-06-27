# Phase 1 Forecaster — Model Evaluation Report

_Generated: 2026-06-22_

Methodology: chronological train / validation / test. Walk-forward cross-validation (4 folds) on the development set selects the model; the final 12-month held-out test set (never seen during selection) gives the unbiased accuracy below. The serving forecasters retrain on all history for live prediction.

Skill score = 1 − MAE_model / MAE_naive; positive means the model beats naive 24-hour persistence.

---
### DA Price (MIBEL PT)

- Development: 47,135 rows (2020-01-15 → 2025-05-31)
- Test (held out): 8,761 rows (2025-05-31 → 2026-05-31)
- Selected model: **Ridge**

**Walk-forward CV (validation MAE — used for selection):**

| Model | MAE (EUR/MWh) |
|-------|-----------|
| Naive persistence | 16.11 |
| Ridge regression  | 12.77 |
| LightGBM          | 15.21 |

**Held-out TEST (unbiased — selection never saw this):**

| Metric | Value |
|--------|-------|
| MAE  | 20.61 EUR/MWh |
| RMSE | 27.62 EUR/MWh |
| Bias (ME) | -0.97 EUR/MWh |
| Skill vs naive | +14.3% |

---

### PV — Global Horizontal Irradiance

- Development: 87,503 rows (2015-01-08 → 2024-12-31)
- Test (held out): 8,761 rows (2024-12-31 → 2025-12-31)
- Selected model: **LightGBM**

**Walk-forward CV (validation MAE — used for selection):**

| Model | MAE (W/m2) |
|-------|-----------|
| Naive persistence | 16.96 |
| Ridge regression  | 15.25 |
| LightGBM          | 12.84 |

**Held-out TEST (unbiased — selection never saw this):**

| Metric | Value |
|--------|-------|
| MAE  | 51.45 W/m2 |
| RMSE | 94.60 W/m2 |
| Bias (ME) | +19.85 W/m2 |
| Skill vs naive | -33.1% |

---

### PV — Ambient Temperature

- Development: 87,503 rows (2015-01-08 → 2024-12-31)
- Test (held out): 8,761 rows (2024-12-31 → 2025-12-31)
- Selected model: **Ridge**

**Walk-forward CV (validation MAE — used for selection):**

| Model | MAE (degC) |
|-------|-----------|
| Naive persistence | 2.26 |
| Ridge regression  | 1.62 |
| LightGBM          | 1.64 |

**Held-out TEST (unbiased — selection never saw this):**

| Metric | Value |
|--------|-------|
| MAE  | 5.72 degC |
| RMSE | 6.59 degC |
| Bias (ME) | -0.42 degC |
| Skill vs naive | -228.3% |

---
