# IDA Price Forecaster — Evaluation Report

Generated: 2026-06-23

## Data
- Source: `ida2_training_data_2024_2025.xlsx` (OMIE/ENTSO-E SIDC intraday results)
- Range : 2024-06-13 to 2025-12-31
- Gate   : IDA2 (H3-H24, closes D-1 22:00 CET; H1-H2 frozen after IDA1)
- Model : gate-specific spread model (Ridge or LightGBM, auto-selected by walk-forward CV)
- Target: spread = price_IDA - price_DA [EUR/MWh]

## Walk-forward CV (2024-06-13 to 2024-12-31, 4 folds)
| Model | MAE EUR/MWh (spread) |
|---|---|
| Naive | 6.7867 |
| Ridge | 5.5217 **SELECTED** |
| LightGBM | 6.1729 |

## Hold-out Test (2025)
| Metric | Value |
|---|---|
| Naive MAE (spread=0) | 6.8646 EUR/MWh |
| Ridge MAE | 5.5179 EUR/MWh |
| Skill score | +19.6% |

*Positive skill: model improves on naive (IDA=DA) baseline*

## Per-Hour-Bucket Test Breakdown
| Bucket | Naive MAE | Ridge MAE | Skill |
|---|---|---|---|
| Off-peak (H3-H6, H23-H24) | 6.76 | 5.62 | +16.9% |
| Peak (H7-H22) | 6.90 | 5.49 | +20.5% |

## IDA2 Gate (Production)
- Tradable hours: H3-H24 (H1-H2 frozen after IDA1; gate closes D-1 22:00 CET)
- Dedicated model trained on IDA2 SIDC clearing prices only
- Baseline for IDA3 re-optimisation