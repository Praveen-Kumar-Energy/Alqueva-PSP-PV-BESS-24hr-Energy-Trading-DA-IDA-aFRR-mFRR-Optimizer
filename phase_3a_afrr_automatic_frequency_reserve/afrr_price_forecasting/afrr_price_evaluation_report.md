# aFRR Cap-Price Forecaster — Evaluation Report

Generated: 2026-06-23

## Data
- Source: `afrr_training_data_2019_2025.xlsx` (REN/eSIO aFRR clearing prices — synthetic proxy)
- Range : 2019-01-01 to 2025-12-31
- Gate  : aFRR capacity market (H1-H24, daily auction, gate closes D-1 before DA)
- Models: two separate models — cap_up (upward reserve) and cap_dn (downward reserve)
- Target: cap_up_EUR_MW, cap_dn_EUR_MW (availability payment, not energy)

## cap_up Model
| Model | CV MAE EUR/MW |
|---|---|
| Naive | 8.4409 |
| Ridge | 6.8243 **SELECTED** |
| LightGBM | 7.1983 |

| Metric | Value |
|---|---|
| Naive MAE | 8.4507 EUR/MW |
| Ridge MAE | 6.7391 EUR/MW |
| Skill score | +20.3% |

## cap_dn Model
| Model | CV MAE EUR/MW |
|---|---|
| Naive | 4.2753 |
| Ridge | 3.7469 **SELECTED** |
| LightGBM | 3.9076 |

| Metric | Value |
|---|---|
| Naive MAE | 4.2921 EUR/MW |
| Ridge MAE | 3.7101 EUR/MW |
| Skill score | +13.6% |

## aFRR Gate (Production)
- Daily capacity auction: offer submitted D-1 before DA gate (gate closes ~D-1 08:00 CET)
- cap_up > 0: plant commits headroom above committed energy to provide upward reserve
- cap_dn > 0: plant commits headroom below committed energy to provide downward reserve
- No MW sold twice (PR-11): reserve headroom bounded by energy position
- FAT: 5 minutes (PICASSO harmonised, since 4 Dec 2024)
- Cap ceiling: 250 EUR/MW (REN regulatory cap)