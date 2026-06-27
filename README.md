# Alqueva PSP + PV + BESS — 24-Hour Energy Trading Optimizer

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/Solver-CPLEX%2022.1-green?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/Optimisation-MILP-orange?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/Markets-DA%20%7C%20IDA%20%7C%20XBID%20%7C%20aFRR%20%7C%20mFRR-purple?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/Grid-MIBEL%20%2F%20OMIE-red?style=for-the-badge"/>
</p>

<p align="center">
  <b>Production-grade 24-hour MILP trading optimizer for the Alqueva hybrid energy plant (Portugal / MIBEL)</b><br/>
  Pumped-Storage Hydro · Floating PV · Battery Storage · DA/IDA/XBID/aFRR/mFRR · Full Settlement & Analytics
</p>

---

## Plant

| Asset | Specification | Status |
|-------|--------------|--------|
| **PSP** | 4 × reversible Francis units — 129.6 MW turbine / 111.6 MW pump each → **518.4 MW gen / 446.4 MW pump** | Confirmed / pump estimated |
| **PV** | 5 MWp floating solar array (commissioned 2022) | Confirmed |
| **BESS** | 1 MW / 2 MWh · SOC 10 %–95 % · η_c = η_d = 0.90 | Confirmed |
| **Upper reservoir** | Alqueva — 3,150 Mm³ usable · head range 54.7–73.0 m | Confirmed |
| **Lower reservoir** | Pedrógão — 54 Mm³ usable (binding constraint on long pumping sequences) | Confirmed |

> **Sign convention everywhere:** generation / discharge = **+** · pumping / charging = **−**

---

## Market Coverage

| Gate | Exchange | Closes (CET) | Scope |
|------|----------|-------------|-------|
| **DA** | OMIE | D-1 12:00 | All 24 hours |
| **IDA1** | OMIE SIDC | D-1 15:00 | H1–H24 |
| **IDA2** | OMIE SIDC | D-1 22:00 | H3–H24 |
| **IDA3** | OMIE SIDC | D 10:00 | H12–H24 (H1–H11 frozen) |
| **XBID** | SIDC continuous | H-1 rolling | Open hours only |
| **aFRR** | PICASSO | DA + 1 h | Symmetric up/dn · FAT = 5 min |
| **mFRR** | MARI | DA + 1 h | Symmetric up/dn · FAT = 12.5 min |
| **Imbalance** | REN | Post-delivery | Long → DA×0.85 · Short → DA×1.20 |

**Key regulatory dates encoded:**
- SIDC reform: 6 → 3 intraday auctions from **13 Jun 2024**
- ISP: 15-minute (96/day) from **19 Mar 2025**
- aFRR (PICASSO) harmonised: **4 Dec 2024** · cap ≤ 250 EUR/MW (REN)
- mFRR (MARI): REN joined **27 Nov 2024**
- FCR: mandatory & non-remunerated in PT/ES — modelled as reserved headroom, never a market gate

---

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         common_layer/                               │
│  configuration/   physical_plant_models/   optimisation_model/      │
│  database/        gate_scheduler/          utilities/               │
└────────────────────────────┬────────────────────────────────────────┘
                             │  shared MILP core + physics
          ┌──────────────────┼──────────────────────────────┐
          ▼                  ▼                              ▼
┌─────────────────┐ ┌───────────────────┐ ┌───────────────────────┐
│  Phase 1        │ │  Phase 2a/2b/2c   │ │  Phase 2d             │
│  DA Bidding     │ │  IDA1 / IDA2/IDA3 │ │  XBID Continuous      │
│  D-1 12:00 CET  │ │  15:00/22:00/10:00│ │  H-1 rolling          │
└────────┬────────┘ └────────┬──────────┘ └──────────┬────────────┘
         │                   │                        │
         └───────────────────┴────────────────────────┘
                             │  committed net position
          ┌──────────────────┼──────────────────────────────┐
          ▼                  ▼                              ▼
┌─────────────────┐ ┌───────────────────┐ ┌───────────────────────┐
│  Phase 3a       │ │  Phase 3b         │ │  Phase 4a             │
│  aFRR Offers    │ │  mFRR Offers      │ │  ISP Real-Time        │
│  PICASSO        │ │  MARI             │ │  Dispatch             │
└────────┬────────┘ └────────┬──────────┘ └──────────┬────────────┘
         │  TSO activation signals        │
         ▼                  ▼            ▼
┌─────────────────┐ ┌───────────────────┐
│  Phase 4b       │ │  Phase 4c         │
│  aFRR Activation│ │  mFRR Activation  │
└────────┬────────┘ └────────┬──────────┘
         │                   │
         └─────────┬─────────┘
                   ▼
     ┌─────────────────────────────┐
     │  Phase 5a · 5b · 5c        │
     │  DA+IDA · Reserve ·        │
     │  Imbalance Settlement      │
     └──────────────┬─────────────┘
                    ▼
     ┌──────────────────────────────┐
     │  Phase 5d Analytics          │
     │  Daily P&L · KPIs · Excel    │
     │  9 Production Figures        │
     └──────────────┬───────────────┘
                    ▼
     ┌──────────────────────────────┐
     │  Phase 6 Backtesting         │
     │  Historical replay ·         │
     │  Forecast & MILP validation  │
     └──────────────────────────────┘
```

---

## MILP Core

One model drives **every gate** — DA, IDA1, IDA2, IDA3, XBID all solve the same 24-hour MILP. Gates differ only in price/forecast inputs and which hours are frozen to the already-committed position. Physics lives in exactly one place (`core_milp_builder.py`) so a constraint fix propagates everywhere.

**Decision variables (per hour):**

| Variable | Description |
|----------|-------------|
| `p_turb[u,h]` / `p_pump[u,h]` | Turbine / pump power (MW) |
| `on_turb[u,h]` / `on_pump[u,h]` | Mode binaries |
| `H_net[h]` | Dynamic hydraulic head (m) — linear in reservoir volume |
| `omega_trb/pmp[u,fi,hi,h]` | Bilinear interpolation weights — 5×5 efficiency surface |
| `pv_used[h]` / `pv_to_bess[h]` / `pv_curt[h]` | PV disposition |
| `p_chg[h]` / `p_dis[h]` / `soc[h]` | BESS charge / discharge / state of charge |
| `v_up[h]` / `v_low[h]` | Reservoir volumes (hm³) |
| `p_net[h]` | Net grid injection = bid quantity |

**Key physical formulas:**

```
Head model:   H_net[h] = 54.7 + 7.89e-9 × (v_up[h] × 1e6 − 830e6)   [m]
Net position: p_net[h] = PSP_net[h] + pv_used[h] + p_dis[h] − p_chg[h]
Water balance: ΔV_upper = (inflow + q_pump − q_turb − spill) × dt / 1e6
aFRR eff_isp_h = (15 − 2.5) / 60 = 0.208333 h   (FAT = 5 min)
mFRR eff_isp_h = (15 − 6.25) / 60 = 0.145833 h  (FAT = 12.5 min)
```

**McCormick linearisation** of the bilinear `H_net × on_binary` products (4 envelope constraints per unit per mode per hour) enables exact MILP solve without spatial branching.

---

## Output Figures

Nine production figures are generated automatically after every pipeline run (`figures/output/`):

| Figure | Description |
|--------|-------------|
| `fig01_dispatch_profile.png` | DA net position (MWh) + DA price (EUR/MWh) |
| `fig02_soc_trajectory.png` | BESS SoC (% of 2 MWh) with 10%/95% bounds |
| `fig03_revenue_waterfall.png` | Revenue by market: DA · IDA+XBID · aFRR · mFRR · Imbalance |
| `fig04_reserve_capacity.png` | aFRR + mFRR capacity offered (MW up/dn per hour) |
| `fig05_gate_position_comparison.png` | Position evolution: DA → IDA1 → IDA2 → IDA3 → XBID |
| `fig06_intraday_reoptimisation.png` | DA vs final committed position (IDA+XBID delta) |
| `fig07_psp_dispatch.png` | PSP turbine/pump MW schedule vs DA price |
| `fig08_pv_bess_flow.png` | PV disposition (used/to-BESS/curtailed) + BESS power |
| `ops_board.png` | 9-panel operations summary dashboard |

---

## Excel Report

Daily report saved to `runtime/reports/daily_report_{date}.xlsx` — 5 sheets:

| Sheet | Content |
|-------|---------|
| `Dispatch_Hourly` | 24 rows × 94 columns — full hourly dispatch for every asset and market |
| `ISP_Activation` | 96 ISP rows — aFRR/mFRR activation records with ramp-corrected energy |
| `Gate_Decisions` | One row per gate — MILP objective, threshold, accept/reject |
| `Summary_KPIs` | 10 sections — revenues, costs, utilisation, reserve, risk metrics |
| `Glossary` | All 94 column definitions with units |

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure solver path
# Edit config/solver.yaml → solver.executable

# Run a full trading day (auto mode)
python run_production.py --date 2026-06-22 --auto

# Run a full trading day (interactive — DA requires [A]/[R] approval)
python run_production.py --date 2026-06-22

# Run individual gates
python phase_1_da_day_ahead_bidding/run_da.py          --date 2026-06-22
python phase_2a_ida1_intraday_auction_1/run_ida1.py    --date 2026-06-22
python phase_2b_ida2_intraday_auction_2/run_ida2.py    --date 2026-06-22
python phase_2c_ida3_intraday_auction_3/run_ida3.py    --date 2026-06-22
python phase_2d_xbid_continuous_intraday/run_xbid.py   --date 2026-06-22
python phase_3a_afrr_automatic_frequency_reserve/run_afrr.py  --date 2026-06-22
python phase_3b_mfrr_manual_frequency_reserve/run_mfrr.py     --date 2026-06-22
python phase_5d_analytics_and_reporting/run_analytics.py      --date 2026-06-22
python phase_6_backtesting_and_validation/run_backtest.py     --start 2026-06-01 --days 7

# Run tests
pytest tests/ -v
```

All outputs (positions, reserve, activations, audit, Excel, figures) write to `runtime/`.

---

## Solver Configuration

CPLEX is called as an **executable** via Pyomo — no Python binding required.

```yaml
# config/solver.yaml
solver:
  executable: "C:/Program Files/IBM/ILOG/CPLEX_Studio2211/cplex/bin/x64_win64/cplex.exe"
```

If CPLEX cannot be reached the run **stops immediately** — the system never emits a bid from an unsolved model.

---

## Safety Model

Every gate runs a **permanent physical checker** before any position is recorded. It re-derives dispatch from the solved schedule and replays it through the plant models, catching violations of:

- Mode exclusivity (no simultaneous turbine + pump per unit)
- Min stable load / nameplate capacity bounds
- Reservoir and BESS SoC bounds
- Energy balance (INV-1: `p_net = PSP_net + pv_used + p_dis − p_chg`)
- No double-selling (energy + reserve ≤ available headroom)
- FAT deliverability for aFRR and mFRR

Full constraint specification: [`PHASE-1-SPECIFICATION.md`](PHASE-1-SPECIFICATION.md)

> The checker shares the build's blind spots — it is a runtime guard, not a substitute for independent review.

---

## Project Structure

```
Alqueva-PSP-PV-BESS-24hr-Energy-Trading-DA-IDA-aFRR-mFRR-Optimizer/
│
├── common_layer/
│   ├── configuration/              Typed YAML config (plant, market, solver)
│   ├── physical_plant_models/      PSP · PV · BESS · reservoir · FCR physics
│   ├── optimisation_model/         Shared 24h MILP · IDA re-optimiser · reserve
│   ├── database/                   SQLite stores (positions, reserve, delivery, audit)
│   ├── gate_scheduler/             CET gate-time resolver and trigger
│   └── utilities/                  Logging · timezone (CET/WET) · ISP calendar · audit
│
├── phase_1_da_day_ahead_bidding/
├── phase_2a_ida1_intraday_auction_1/
├── phase_2b_ida2_intraday_auction_2/
├── phase_2c_ida3_intraday_auction_3/
├── phase_2d_xbid_continuous_intraday/
├── phase_3a_afrr_automatic_frequency_reserve/
├── phase_3b_mfrr_manual_frequency_reserve/
├── phase_4a_isp_real_time_dispatch/
├── phase_4b_afrr_activation_response/
├── phase_4c_mfrr_activation_response/
├── phase_5a_da_ida_settlement/
├── phase_5b_reserve_settlement/
├── phase_5c_imbalance_settlement/
├── phase_5d_analytics_and_reporting/
├── phase_6_backtesting_and_validation/
│
├── figures/                        Figure generator + output PNGs
├── config/                         plant.yaml · market.yaml · solver.yaml · run.yaml
├── tests/                          Full test suite (physics · settlement · e2e · reserve)
├── run_production.py               Master orchestrator — all phases, one delivery date
└── requirements.txt
```

---

## Dependencies

```
pyomo          # MILP model builder
pandas         # Data processing
numpy          # Numerical computation
openpyxl       # Excel report generation
scikit-learn   # Price / PV / inflow ML forecasters
matplotlib     # Production figures
pvlib          # PV irradiance modelling
pytz           # CET / WET timezone handling
```

Full list: [`requirements.txt`](requirements.txt)

---

<p align="center">
  Built for the <b>Alqueva</b> pumped-storage complex · Portugal · MIBEL / OMIE<br/>
  <i>PSP 518.4 MW generation · 446.4 MW pump · 5 MW floating PV · 1 MW / 2 MWh BESS</i>
</p>
