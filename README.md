# Alqueva PSP + PV + BESS — 24-hour Energy Trading Optimizer

Production-grade trading optimizer for the **Alqueva** hybrid plant (Portugal /
MIBEL): a 4 × 129.6 MW reversible pumped-storage station, a 5 MWp floating PV
array, and a 1 MW / 2 MWh battery, traded across **DA, IDA1/2/3, XBID, aFRR and
mFRR** with full settlement and analytics.

One optimisation core (a 24-hour MILP solved with **CPLEX**) drives every energy
gate; reserve, dispatch, settlement and backtesting are layered on top. Every gate
re-validates its output against the physical plant models before anything is
"submitted" (a permanent runtime checker).

---

## Plant (config/plant.yaml)

| Asset | Spec | Source |
|-------|------|--------|
| PSP | 4 reversible Francis units, 129.6 MW turbine / 111.6 MW pump each | CONFIRMED / pump ESTIMATED |
| PV | 5 MWp floating solar (2022) | CONFIRMED |
| BESS | 1 MW / 2 MWh, SOC 10–95 %, 0.9×0.9 round-trip | CONFIRMED |
| Upper reservoir | Alqueva, 3,150 Mm³ usable | CONFIRMED |
| Lower reservoir | Pedrógão, 54 Mm³ usable (binds on long pumping) | CONFIRMED |

Sign convention everywhere: **generation/discharge = +, pumping/charging = −.**

---

## Architecture

```
common_layer/
  configuration/         typed plant/market/solver config from YAML
  physical_plant_models/ PSP / PV / BESS / reservoir / FCR physics + validation
  optimisation_model/    the shared 24h MILP, IDA re-optimiser, reserve sizing/activation
  utilities/             logging, market(CET)/plant(Lisbon) tz, calendar/ISP, audit
  database/              positions, reserve, delivery/activations (SQLite), audit
  gate_scheduler/        resolve & fire daily gates at CET times

phase_1_da_day_ahead_bidding/        DA bid (MILP) + Phase-3A checker + [A]/[R] approval
phase_2a/2b/2c_ida1/2/3...           intraday auctions (re-optimise, no-churn threshold)
phase_2d_xbid_continuous_intraday/   continuous intraday, per-order caps
phase_3a_afrr.../ 3b_mfrr...          reserve capacity offers from leftover headroom
phase_4a_isp_real_time_dispatch/     ISP setpoints + delivery
phase_4b/4c_afrr/mfrr_activation/    TSO activation within FAT
phase_5a/5b/5c_*settlement/          DA+IDA / reserve / imbalance settlement
phase_5d_analytics_and_reporting/    daily P&L, KPIs, Excel report
phase_6_backtesting_and_validation/  replay + forecast/MILP validation
RUN_PRODUCTION.py                    master orchestrator (all phases, one date)
```

**Why one core MILP?** DA and every IDA solve the *same* physical model — they
differ only in prices and which hours are frozen to the committed position. The
physics lives once (`optimisation_model/core_milp_builder.py`), so a constraint
fix propagates to every gate.

---

## Market facts encoded (all verifiable)

- **Gate times (CET):** DA D-1 12:00 · IDA1 D-1 15:00 · IDA2 D-1 22:00 · IDA3 D 10:00 (hours **12–24 only**) · XBID closes 1 h before delivery.
- **SIDC reform:** 6 → 3 intraday auctions from **13 Jun 2024**.
- **ISP:** 15-minute (96/day) from **19 Mar 2025**.
- **aFRR FAT 5 min** (PICASSO, harmonised 4 Dec 2024); cap ≤ **250 EUR/MW** (REN).
- **mFRR FAT 12.5 min** (MARI; REN joined **27 Nov 2024**).
- **FCR is mandatory & non-remunerated** in PT/ES — modelled as reserved headroom, **never a market gate**.

---

## Run it

```bash
# Full trading day, hands-free
python RUN_PRODUCTION.py --date 2026-06-22 --auto

# Full trading day, interactive demo (DA = [A]/[R], other gates = ENTER pause)
python RUN_PRODUCTION.py --date 2026-06-22

# Or one gate at a time
python phase_1_da_day_ahead_bidding/run_da.py --date 2026-06-22
python phase_2a_ida1_intraday_auction_1/run_ida1.py --date 2026-06-22
python phase_3a_afrr_automatic_frequency_reserve/run_afrr.py --date 2026-06-22
python phase_5d_analytics_and_reporting/run_analytics.py --date 2026-06-22
python phase_6_backtesting_and_validation/run_backtest.py --start 2026-06-01 --days 7
```

Outputs (positions, reserve, audit, Excel reports) are written under `runtime/`.

---

## Solver

CPLEX is called as an **executable** via Pyomo (no Python binding required, so it
runs under Python 3.14). Set the path in `config/solver.yaml`:

```yaml
solver:
  executable: "C:/Program Files/IBM/ILOG/CPLEX_Studio2211/cplex/bin/x64_win64/cplex.exe"
```

If CPLEX cannot be reached the run **stops** — it never emits a bid from an
unsolved model.

---

## Safety model

Each gate, before "submitting", runs a **permanent physical checker** that
re-derives the dispatch from the solved schedule and replays it through the same
plant models — catching any violation of: mode exclusivity, min stable load,
reservoir/SOC bounds, energy balance, no-MW-sold-twice (energy + reserve ≤
headroom), and FAT deliverability. The full requirement/prohibition/invariant
list is in `PHASE-1-SPECIFICATION.md`.

> The checker shares this build's blind spots — it is a runtime guard, not a
> substitute for an independent review.

Dependencies: see `requirements.txt`.
