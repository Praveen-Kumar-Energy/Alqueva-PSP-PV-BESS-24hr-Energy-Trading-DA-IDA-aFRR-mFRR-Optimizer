<h1 align="center">Dr. Polamarasetty Praveen Kumar</h1>

<p align="center">
  <b>Energy Asset Optimisation &nbsp;·&nbsp; Multi-Market Bidding &nbsp;·&nbsp; MILP Dispatch &nbsp;·&nbsp; Risk Analytics</b>
</p>

<p align="center">
  <a href="mailto:praveenindia.p@gmail.com"><img src="https://img.shields.io/badge/Email-praveenindia.p%40gmail.com-red?style=for-the-badge&logo=gmail&logoColor=white"/></a>
  <a href="https://linkedin.com/in/polamarasetty-praveen-kumar-2a3a422a1"><img src="https://img.shields.io/badge/LinkedIn-Connect-blue?style=for-the-badge&logo=linkedin&logoColor=white"/></a>
  <a href="https://scholar.google.com/citations?user=8EFqCyAAAAAJ"><img src="https://img.shields.io/badge/Google%20Scholar-H--Index%2022-4285F4?style=for-the-badge&logo=googlescholar&logoColor=white"/></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Available-July%202026-brightgreen?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/EU%20Work%20Authorization-Portugal-blue?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/Publications-70%2B%20%7C%201%2C500%2B%20citations-orange?style=for-the-badge"/>
</p>

---

## About Me

Energy systems researcher and industrial-grade software engineer with deep expertise in **hybrid plant optimisation, multi-market bidding and scheduling, and MILP-based dispatch**.

Built a **14-gate multi-market MILP bidding and scheduling system** (DA + IDA1/2/3 + XBID + aFRR + mFRR + ISP dispatch + settlement + analytics) for the real **Alqueva hybrid plant** (518.4 MW PSP + 5 MW PV + 1 MW / 2 MWh BESS, Portugal / MIBEL). System uses IBM CPLEX (avg solve **0.172 s**, mip_gap=0.005), auto-selects ML forecasters (Naive / Ridge / LightGBM via walk-forward CV), and applies a permanent **13-invariant physical checker** that blocks any bid with a constraint violation before it propagates. Risk framework includes historical VaR/CVaR (95 % & 99 %) and Monte Carlo bootstrap confidence intervals (n=10,000).

---

## ⚡ Flagship Project — Alqueva 24-Hour Energy Trading Optimizer

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/Solver-CPLEX%2022.1-brightgreen?style=for-the-badge&logo=ibm&logoColor=white"/>
  <img src="https://img.shields.io/badge/Fallback-HiGHS%20%7C%20CBC-green?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/Optimisation-MILP-orange?style=for-the-badge"/>
  <br/>
  <img src="https://img.shields.io/badge/Markets-DA%20%7C%20IDA1--3%20%7C%20XBID%20%7C%20aFRR%20%7C%20mFRR-purple?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/Grid-MIBEL%20%2F%20OMIE%20%2F%20REN-red?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/Pipeline-15%20phases%20%7C%20125s-gold?style=for-the-badge"/>
</p>

### Plant

| Asset | Specification |
|-------|--------------|
| **PSP — Pumped Storage** | 4 × 129.6 MW reversible Francis units · **518.4 MW generation / 446.4 MW pumping** |
| **PV — Floating Solar** | 5 MWp · commissioned 2022 · temperature derate + annual degradation model |
| **BESS — Battery** | 1 MW / 2 MWh · SOC 10–95 % · η_charge = η_discharge = 0.90 |
| **Upper Reservoir** | Alqueva · 3,150 hm³ usable · head range 54.7–73.0 m |
| **Lower Reservoir** | Pedrógão · 54 hm³ usable · binding constraint on long pumping sequences |

### 15-Phase Pipeline

| Phase | Gate | Key Output |
|-------|------|-----------|
| **1 — DA** | OMIE D-1 12:00 | H1–H24 energy bids · +€127,767 revenue |
| **2A — IDA1** | OMIE SIDC D-1 15:00 | Position delta re-optimisation · H1–H24 |
| **2B — IDA2** | OMIE SIDC D-1 22:00 | Position delta · H3–H24 |
| **2C — IDA3** | OMIE SIDC D 10:00 | Position delta · H12–H24 (H1–H11 frozen) |
| **2D — XBID** | SIDC continuous H-1 | Cross-border continuous intraday |
| **3A — aFRR** | PICASSO DA+1h | Symmetric ±MW capacity · FAT 5 min · +€103,324 |
| **3B — mFRR** | MARI DA+1h | Symmetric ±MW capacity · FAT 12.5 min · +€23,518 |
| **4A — RT Dispatch** | 96 ISPs × 15 min | REN signal simulation |
| **4B — aFRR Activation** | Real-time | Activation response |
| **4C — mFRR Activation** | Real-time | Activation response |
| **5A — Energy Settlement** | Post-delivery | DA / IDA imbalance reconciliation |
| **5B — Reserve Settlement** | Post-delivery | aFRR / mFRR payment |
| **5C — Imbalance Settlement** | Post-delivery | REN 15-min ISP penalty calculation |
| **5D — Analytics** | End-of-day | KPI report · 5-sheet Excel · 9 figures |

```
$ python run_production.py --auto --synthetic

  PHASE                                          STATUS     TIME  NOTE
  1      Day-Ahead bidding  (OMIE DA)           [ OK  ]  50.65s  energy revenue +127,767
  2A     IDA1 intraday re-optimisation          [ OK  ]  10.78s
  2B     IDA2 intraday re-optimisation          [ OK  ]  11.79s
  2C     IDA3 intraday re-optimisation          [ OK  ]  10.70s
  2D/W1  XBID continuous  (D-1 18:30)           [ OK  ]   9.96s
  3A     aFRR capacity offer  (PICASSO/REN)     [ OK  ]  10.83s  capacity revenue +103,324
  3B     mFRR capacity offer  (MARI)            [ OK  ]   3.97s  capacity revenue +23,518
  4A     RT dispatch simulation  (96 ISPs)      [ OK  ]   0.03s
  4B     aFRR activation response               [ OK  ]   0.21s
  4C     mFRR activation response               [ OK  ]   0.13s
  5A     Energy settlement  (DA / IDA)          [ OK  ]   1.89s
  5B     Reserve settlement  (aFRR / mFRR)      [ OK  ]   0.04s
  5C     Imbalance settlement  (REN balance)    [ OK  ]   0.18s
  5D     Analytics + KPI report + Excel         [ OK  ]   2.15s  total pnl +312,886

  PIPELINE COMPLETE — 15 passed   0 failed   (125.5s total)
```

### Architecture Highlights

- **Single shared MILP model** — `core_milp_solver.py` used by all 15 phases; each phase passes its own horizon, frozen variables, and market constraints. IBM CPLEX avg solve **0.172 s**, mip_gap = 0.005
- **`--from-phase` hot-restart** — re-enter at any phase without re-running earlier phases; production-safe for intraday gate re-optimisation
- **ComponentStore** — typed dataclass store (GateResults, DispatchPlan, SettlementSheet) shared across phases; zero file I/O between phases
- **13-invariant physical checker** — re-derives dispatch from solved schedule; checks mode exclusivity, min stable load, ramp rates, reservoir bounds, BESS SoC, energy balance, no-double-sell, FAT deliverability; raises `BidCheckError` on any violation before propagation
- **ML forecasting pipeline** — DA price, aFRR, mFRR, PV power, water inflow forecasters each auto-select best model (Naive / Ridge / LightGBM) via 4-fold walk-forward CV; 17 engineered features for DA price (lag 24h/48h/168h/336h, rolling mean/std, cyclical calendar)
- **Risk analytics** — historical VaR (95%/99%) and CVaR/Expected Shortfall; Monte Carlo bootstrap (n=10,000 resamples); Sharpe ratio (annualised); max drawdown; P&L attribution by stream (DA / IDA / aFRR / mFRR / imbalance)
- **Immutable audit trail** — append-only JSON-lines log of every decision (solve / check / approve / submit) with market-time timestamps; designed for regulatory inspection
- **CPLEX 22.1 primary / HiGHS fallback** — automatic solver detection; same model runs on any machine
- **15-min ISP resolution** — 96 intervals/day matching REN grid operator standard (Mar 2025 regulatory)

### Daily P&L Summary

| Revenue Stream | Amount |
|---------------|--------|
| DA energy trading (OMIE) | +€127,767 |
| aFRR capacity (PICASSO) | +€103,324 |
| mFRR capacity (MARI) | +€23,518 |
| IDA re-optimisation + XBID | included |
| Imbalance settlement | included |
| **Total P&L** | **+€312,886** |

---

## 🛠️ Technical Skills

| Area | Detail |
|------|--------|
| **Optimisation** | MILP formulation · Pyomo · IBM CPLEX (mip_gap=0.005, avg 0.172 s) · HiGHS · CBC · unit commitment · piecewise-linear turbine/pump curves · McCormick linearization · 24-hour scheduling horizon |
| **Energy Markets** | OMIE DA/IDA · SIDC IDA1/2/3 (post-Jun 2024 reform) · aFRR via PICASSO (FAT 5 min) · mFRR via MARI (FAT 12.5 min, live 27 Nov 2024) · XBID continuous intraday · ISP imbalance settlement (15-min from 19 Mar 2025) · FCR non-remunerated headroom reservation |
| **Risk & P&L** | VaR & CVaR — historical simulation + Monte Carlo bootstrap (n=10,000) · confidence intervals · Sharpe ratio (annualised) · max drawdown · P&L attribution by stream · pre-trade risk checks · audit logging |
| **ML & Forecasting** | LightGBM · Ridge regression · Naive baseline · walk-forward CV (4 folds) · lag/rolling/cyclical calendar features · DA price, aFRR, mFRR, PV power, inflow forecasters |
| **Programming** | Python · pandas · NumPy · Pyomo · scikit-learn · LightGBM · openpyxl · SQLite · Git/GitHub · YAML config · CLI design · pytest |
| **Systems & Data** | OMIE live API · PICASSO/MARI/REN structured data · ENTSO-E market frameworks · ERA5 reanalysis (PV weather) · SQLite position/reserve/delivery/audit stores · Excel report generation |
| **Asset Knowledge** | Pumped-storage hydropower (Francis reversible units) · floating solar PV · BESS (SoC tracking, cycle degradation) · reservoir dynamics (upper/lower bounds, natural inflow) · head-dependent efficiency curves |

---

## 💼 Professional Experience

### Energy Optimisation Engineer (PostDoc Researcher)
**INESC TEC — Centre for Power and Energy Systems (CPES), Porto, Portugal** · *Feb 2025 – Present*

*STOR-HY (Horizon Europe GA 101172905) · Alqueva Hybrid Plant: 4 × 129.6 MW reversible Francis PSP + 5 MWp Floating PV + 1 MW / 2 MWh BESS*

- **Built a 14-gate end-to-end automated bidding and scheduling pipeline** covering DA bid (OMIE), IDA1/2/3 re-optimisation (SIDC), XBID continuous intraday, aFRR capacity offer (PICASSO), mFRR capacity offer (MARI), ISP dispatch simulation (REN signal architecture), aFRR/mFRR TSO activation, DA/IDA energy settlement, reserve settlement, imbalance settlement, and daily P&L analytics — all orchestrated from a single `run_production.py` with full audit logging; IDA bids are position deltas (post-June-2024 MIBEL reform); IDA3 restricted to hours 12–24; CPLEX avg solve 0.172 s
- **ML forecasting pipeline**: DA price, aFRR price, mFRR price, PV power, and water inflow forecasters each auto-select the best model (Naive / Ridge / LightGBM) via 4-fold walk-forward cross-validation; 17 engineered features for DA price (lag 24h/48h/168h/336h, rolling mean/std, cyclical calendar encodings)
- **Physical plant modelling**: piecewise-linear turbine efficiency curves (4 Francis units, 129.6 MW each); pump flow linearized for MILP compatibility; full upper (Alqueva 3,150 hm³) and lower (Pedrógão 54 hm³) reservoir dynamics with monthly natural inflow; BESS SoC tracking with binary charge/discharge commitment and cycle-weighted degradation cost
- **Permanent 13-invariant physical checker**: re-derives dispatch from solved schedule and replays through plant models before any result propagates — checks mode exclusivity, min stable load, ramp rates, reservoir bounds, BESS SoC, energy balance, no-double-sell, and FAT deliverability; raises `BidCheckError` on any violation
- **Risk analytics framework**: historical VaR (95%/99%) and CVaR/Expected Shortfall from daily P&L distribution; Monte Carlo bootstrap (n=10,000 resamples with replacement) computes confidence intervals on VaR and CVaR; annualised Sharpe ratio and max drawdown; all metrics exported to Excel Risk sheet
- **Immutable audit trail**: append-only JSON-lines logging every decision (solve/check/approve/submit) with market-time timestamps; designed for regulatory inspection and post-trade accountability
- Participated in consortium technical meetings with **EDP Produção, EDP New Energy Technologies, EDF, and GE Vernova** within the STOR-HY Horizon Europe project

---

### Assistant Professor
**GMR Institute of Technology, Rajam, India — Dept. Electrical & Electronics Engineering** · *Jul 2021 – Jan 2025*

Power systems and renewable energy teaching; hybrid energy systems research.

---

### PhD Research Scholar
**IIT Roorkee — Dept. of Hydro and Renewable Energy** · *Jul 2018 – Jul 2021*

- Designed and optimized hybrid renewable energy systems (PV, wind, biomass, BESS) for rural electrification in India
- Applied metaheuristic optimization in MATLAB to minimize LCC, LCOE across multiple battery technologies and dispatch strategies
- Published complete optimization code on MathWorks File Exchange (open-source, reproducible research)

---

## 🎓 Education

| Degree | Institution | Year |
|--------|-------------|------|
| **Ph.D.** — Renewable Energy | IIT Roorkee, India | 2018 – 2021 |
| **M.E.** — Power Electronics Drives & Control | Andhra University, India | 2010 – 2012 |
| **B.Tech** — Electrical & Electronics Engineering | JNTU Kakinada, India | 2006 – 2010 |

---

## 🤝 Industrial Collaborations & Funded Projects

**STOR-HY** · Horizon Europe GA 101172905 · 2025–Present  
Partners: EDP Produção · EDP New Energy Technologies · EDF · GE Vernova  
*Built core MILP optimisation system for the Alqueva hybrid plant*

**SE-HYDRO** · Horizon Europe GA 101269565 · Started May 2026  
16-partner consortium: NTUA (coordinator) · ANDRITZ HYDRO · EDP · GE Vernova · PPC Greece · Smart Innovation Norway  
*Co-authored successful Horizon Europe grant proposal; contributing to AI-based energy management and digital twin frameworks for hydro pilot sites in Greece, France, Portugal and Serbia*

---

## 📚 Research Impact

<p align="center">
  <img src="https://img.shields.io/badge/Publications-70%2B-blue?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/H--Index-22-green?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/Citations-1%2C500%2B-orange?style=for-the-badge"/>
</p>

[Google Scholar Profile](https://scholar.google.com/citations?user=8EFqCyAAAAAJ)

---

<p align="center">
  <i>All code is production-grade Python — modular, typed, config-driven, and built to industrial trading desk standards.</i><br/>
  <b>Available from July 2026 · EU Work Authorization · Porto, Portugal</b>
</p>
