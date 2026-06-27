# mFRR Cap-Price Forecaster — Evaluation Report

Generated: 2026-06-23

## Data
- Source: `mfrr_training_data_2024_2025.xlsx` (MARI mFRR clearing prices — synthetic proxy)
- Range : 2024-11-27 (REN MARI accession) to 2025-12-31
- Gate  : mFRR capacity market (H1-H24, daily auction, gate closes D-1 before DA)
- Models: two separate models — cap_up (upward reserve) and cap_dn (downward reserve)
- Note  : 13 months data; 3 CV folds; 3-month hold-out test (Oct-Dec 2025)

## cap_up Model
| Model | CV MAE EUR/MW |
|---|---|
| Naive | 4.4428 |
| Ridge | 4.0781 **SELECTED** |
| LightGBM | 4.8503 |

| Metric | Value |
|---|---|
| Naive MAE | 4.7442 EUR/MW |
| Ridge MAE | 4.2905 EUR/MW |
| Skill score | +9.6% |

## cap_dn Model
| Model | CV MAE EUR/MW |
|---|---|
| Naive | 3.1689 **SELECTED** |
| Ridge | 3.7875 |
| LightGBM | 3.4225 |

| Metric | Value |
|---|---|
| Naive MAE | 2.9836 EUR/MW |
| Naive MAE | 2.9836 EUR/MW |
| Skill score | +0.0% |

## mFRR Gate (Production)
- Daily capacity auction: offer submitted D-1 before DA gate (gate closes ~D-1 08:00 CET)
- FAT: 12.5 minutes (MARI harmonised, REN joined MARI 27 Nov 2024)
- Platform: MARI (Manually Activated Reserves Initiative)
- mFRR sized from headroom REMAINING after aFRR commitment (PR-11 stack)
- No MW sold twice: mFRR + aFRR headroom bounded by energy position
- Cap ceiling: 250 EUR/MW (REN regulatory cap)
- Independent forecast (not derived from aFRR): MARI and PICASSO are separate markets