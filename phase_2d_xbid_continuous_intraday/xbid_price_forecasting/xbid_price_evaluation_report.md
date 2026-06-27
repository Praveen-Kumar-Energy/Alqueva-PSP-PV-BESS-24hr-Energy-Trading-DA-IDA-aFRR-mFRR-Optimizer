# XBID Price Forecaster — Evaluation Report

Generated: 2026-06-23

## Data
- Source: `xbid_training_data_2024_2025.xlsx` (synthetic XBID mid-price proxy)
- Range : 2024-06-13 to 2025-12-31
- Gate  : XBID continuous (H1-H24; closes 1h before each delivery period)
- Note  : Real XBID order-book data requires commercial EPEX SPOT subscription.
          Proxy = IDA3 clearing + OU spread noise (std ~14 EUR/MWh > IDA3 ~11).
- Model : gate-specific spread model (Ridge or LightGBM, auto-selected by walk-forward CV)
- Target: spread = price_XBID - price_DA [EUR/MWh]

## Walk-forward CV (2024-06-13 to 2024-12-31, 4 folds)
| Model | MAE EUR/MWh (spread) |
|---|---|
| Naive | 10.8514 |
| Ridge | 5.6343 **SELECTED** |
| LightGBM | 6.6262 |

## Hold-out Test (2025)
| Metric | Value |
|---|---|
| Naive MAE (spread=0) | 11.1748 EUR/MWh |
| Ridge MAE | 5.5428 EUR/MWh |
| Skill score | +50.4% |

*Positive skill: model improves on naive (XBID=DA) baseline*

## Per-Hour-Bucket Test Breakdown
| Bucket | Naive MAE | Ridge MAE | Skill |
|---|---|---|---|
| Off-peak (H1-H6, H23-H24) | 10.75 | 5.34 | +50.3% |
| Peak (H7-H22) | 11.36 | 5.63 | +50.4% |

## XBID Gate (Production)
- Tradable hours: H1-H24 (gate closes 1h before each delivery period)
- Two check windows: W1 (D-1 18:30 CET), W2 (D 09:30 CET)
- Per-order cap: xbid_max_volume_per_order_mw (config)
- Order placed only if avg gain > xbid_min_spread_eur_mwh (no-churn)
- Spread std wider than IDA3 (~14 vs ~11 EUR/MWh): closer to delivery