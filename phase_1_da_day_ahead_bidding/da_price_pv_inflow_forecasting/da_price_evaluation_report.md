# DA Price Forecaster — Train / Validation / Test Report

_Generated: 2026-06-22_

## Methodology

Chronological train / validation / test — no shuffling, no future leakage.
Walk-forward CV (4 folds) on the development set selects the model.
The final 12-month held-out test set (never seen during selection) gives the unbiased accuracy below.
The serving forecaster (da_price_forecaster.py) retrains on all history for live daily prediction.

Skill score = 1 − MAE_model / MAE_naive; positive = beats naive 24h persistence.

---

## Data

- Development : 47,135 rows  (2020-01-15 → 2025-05-31)
- Test (held out): 8,761 rows  (2025-05-31 → 2026-05-31)
- Selected model : **Ridge**

---

## Walk-forward CV — Validation MAE (model selection)

| Model | MAE (EUR/MWh) |
|-------|-----------|
| Naive persistence | 16.11 |
| Ridge regression  | 12.77 |
| LightGBM          | 15.21 |

---

## Held-out TEST — Unbiased Accuracy

| Metric | Value |
|--------|-------|
| MAE        | 20.61 EUR/MWh |
| RMSE       | 27.62 EUR/MWh |
| Bias (ME)  | -0.97 EUR/MWh |
| Skill vs naive | +14.3% |
