# IDA Price Forecaster — Evaluation Report

Generated: 2026-06-23

## Data
- Source: `ida1_training_data_2024_2025.xlsx` (OMIE/ENTSO-E SIDC intraday results)
- Range : 2024-06-13 to 2025-12-31
- Gate   : IDA1 (H1-H24, closes D-1 15:00 CET)
- Model : gate-specific spread model (Ridge or LightGBM, auto-selected by walk-forward CV)
- Target: spread = price_IDA - price_DA [EUR/MWh]

## Walk-forward CV (2024-06-13 to 2024-12-31, 4 folds)
| Model | MAE EUR/MWh (spread) |
|---|---|
| Naive | 4.9113 |
| Ridge | 3.9264 **SELECTED** |
| LightGBM | 4.4435 |

## Hold-out Test (2025)
| Metric | Value |
|---|---|
| Naive MAE (spread=0) | 4.9551 EUR/MWh |
| Ridge MAE | 4.1807 EUR/MWh |
| Skill score | +15.6% |

*Positive skill: model improves on naive (IDA=DA) baseline*

## Per-Hour-Bucket Test Breakdown
| Bucket | Naive MAE | Ridge MAE | Skill |
|---|---|---|---|
| Off-peak (H1-H6, H23-H24) | 4.89 | 4.27 | +12.7% |
| Peak (H7-H22) | 4.98 | 4.14 | +16.9% |

## IDA1 Gate (Production)
- Tradable hours: H1-H24 (all hours; gate closes D-1 15:00 CET)
- Dedicated model trained on IDA1 SIDC clearing prices only
- Baseline for IDA2 re-optimisation