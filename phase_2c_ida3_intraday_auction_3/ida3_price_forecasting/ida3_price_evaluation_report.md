# IDA Price Forecaster — Evaluation Report

Generated: 2026-06-23

## Data
- Source: `ida3_training_data_2024_2025.xlsx` (OMIE/ENTSO-E SIDC intraday results)
- Range : 2024-06-13 to 2025-12-31
- Gate   : IDA3 (H12-H24, closes D 10:00 CET; H1-H11 frozen after IDA1/IDA2)
- Model : gate-specific spread model (Ridge or LightGBM, auto-selected by walk-forward CV)
- Target: spread = price_IDA - price_DA [EUR/MWh]

## Walk-forward CV (2024-06-13 to 2024-12-31, 4 folds)
| Model | MAE EUR/MWh (spread) |
|---|---|
| Naive | 9.4290 |
| Ridge | 7.7134 **SELECTED** |
| LightGBM | 8.5138 |

## Hold-out Test (2025)
| Metric | Value |
|---|---|
| Naive MAE (spread=0) | 9.2666 EUR/MWh |
| Ridge MAE | 7.5048 EUR/MWh |
| Skill score | +19.0% |

*Positive skill: model improves on naive (IDA=DA) baseline*

## Per-Hour-Bucket Test Breakdown
| Bucket | Naive MAE | Ridge MAE | Skill |
|---|---|---|---|
| Midday (H12-H16) | 8.99 | 7.40 | +17.7% |
| Evening (H17-H24) | 9.37 | 7.55 | +19.5% |

## IDA3 Gate (Production)
- Tradable hours: H12-H24 (H1-H11 frozen after IDA1/IDA2; gate closes D 10:00 CET)
- Dedicated model trained on IDA3 SIDC clearing prices only
- Final intraday gate; no further re-optimisation after this