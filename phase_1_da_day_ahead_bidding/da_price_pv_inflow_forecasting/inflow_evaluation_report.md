# Inflow Forecaster — Train / Validation / Test Report

_Generated: 2026-06-23_

## Methodology

Chronological train / validation / test — no shuffling, no future leakage.
Walk-forward CV (4 folds) on development set selects the model.
Final 12-month held-out test set gives unbiased accuracy below.
Source: 11-year hourly research dataset (2015-2025), aggregated to daily means.
Serving forecaster (inflow_forecaster.py) retrains on full history daily.

Skill score = 1 − MAE_model / MAE_naive; positive = beats naive 1-day persistence.

---

## Data

- Development : 3,622 days  (2015-01-31 → 2024-12-30)
- Test (held out): 366 days  (2024-12-31 → 2025-12-31)
- Selected model : **Ridge**

---

## Walk-forward CV — Validation MAE (model selection)

| Model | MAE (m3/h) |
|-------|-----------|
| Naive persistence | 114034 |
| Ridge regression  | 81234 |
| LightGBM          | 91028 |

---

## Held-out TEST — Unbiased Accuracy

| Metric | Value |
|--------|-------|
| MAE        | 416580 m3/h |
| RMSE       | 741729 m3/h |
| Bias (ME)  | -318721 m3/h |
| Skill vs naive | -53.3% |
