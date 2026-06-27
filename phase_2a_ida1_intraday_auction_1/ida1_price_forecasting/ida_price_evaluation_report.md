# IDA Price Forecaster — Evaluation Report

Generated: 2026-06-23

## Data
- Source: `ida_training_data_2020_2025.xlsx` (synthetic MIBEL-representative)
- Range : 2020-01-01 to 2025-12-31
- Sessions: SI1 (lead 24h) through SI6 (lead 1h)
- Model : lead-time spread model — one model, `lead_time_h` as feature
- Target: spread = price_IDA - price_DA [EUR/MWh]

## Walk-forward CV (2020-2024, 4 folds)
| Model | MAE EUR/MWh (spread) |
|---|---|
| Naive | 7.7315 |
| Ridge | 6.1779 **SELECTED** |
| LightGBM | 6.4257 |

## Hold-out Test (2025)
| Metric | Value |
|---|---|
| Naive MAE (spread=0) | 7.2150 EUR/MWh |
| Ridge MAE | 5.7916 EUR/MWh |
| Skill score | +19.7% |

*Positive skill: model improves on naive (IDA=DA) baseline*

## Per-session Test Breakdown
| Session | Lead time | Naive MAE | Ridge MAE | Skill |
|---|---|---|---|---|
| SI1 | 24h | 4.96 | 3.94 | +20.6% |
| SI2 | 16h | 6.86 | 5.46 | +20.4% |
| SI3 | 9h | 9.27 | 7.42 | +19.9% |
| SI4 | 6h | 11.90 | 9.77 | +17.9% |
| SI5 | 3h | 14.04 | 11.68 | +16.8% |
| SI6 | 1h | 16.38 | 15.11 | +7.8% |

## IDA Gate Mapping (Production)
| Gate | Lead time used | Sessions trained on |
|---|---|---|
| IDA1 | 24h | SI1, SI2, SI3, SI4, SI5, SI6 |
| IDA2 | 16h | SI1, SI2, SI3, SI4, SI5, SI6 |
| IDA3 |  9h | SI1, SI2, SI3, SI4, SI5, SI6 |

One model, all sessions pooled. `lead_time_h` is the key feature that
interpolates session volatility to the 3 SIDC IDA gate times.