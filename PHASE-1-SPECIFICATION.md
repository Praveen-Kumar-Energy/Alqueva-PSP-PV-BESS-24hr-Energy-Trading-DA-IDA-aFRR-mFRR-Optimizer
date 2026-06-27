# Phase 1 — Specification

**System:** Alqueva PSP + PV + BESS — 24-hour Energy Trading Optimizer
**Markets:** DA, IDA1/2/3, XBID, aFRR, mFRR, Imbalance settlement (Portugal / MIBEL)
**Date written:** 2026-06-21
**Status:** AWAITING USER APPROVAL — no code until approved

This document is the contract. Phase 3 verification (Step A automated checker + Step B
adversarial review) tests the code against every requirement, prohibition, and invariant below.
If the code violates any item here, the code is wrong — not the spec.

---

## 0. Plant Physical Definition (from 24hr_optimizer config — CONFIRMED values)

| Asset | Parameter | Value | Source status |
|-------|-----------|-------|---------------|
| PSP | Units | 4 reversible Francis pump-turbines | CONFIRMED |
| PSP | Turbine max (net) | 129.6 MW/unit (518.4 MW total) | CONFIRMED |
| PSP | Turbine min stable | 57.0 MW/unit | CONFIRMED |
| PSP | Pump max | 111.6 MW/unit (446.4 MW total) | ESTIMATED — flag in code |
| PV | Floating solar | 5.0 MW peak | CONFIRMED (2022) |
| BESS | Power / Energy | 1.0 MW / 2.0 MWh | CONFIRMED (XFLEX Hydro) |
| BESS | SOC window | 10% – 95% (0.20 – 1.90 MWh) | CONFIRMED config |
| BESS | Round-trip | 0.90 charge × 0.90 discharge | CONFIRMED config |
| Upper reservoir | Usable | 3,150 Mm³ (4,150 total) | CONFIRMED |
| Lower reservoir | Pedrógão usable | 54 Mm³ | CONFIRMED — binding on long pumping |
| Plant | Max net generation | +525.4 MW | derived |
| Plant | Max net demand (pump) | −446.4 MW | derived (estimated pump) |

Plant net power sign convention: **generation = positive, pumping/charging = negative.**

---

## 1. Functional Requirements (what the system MUST do)

### FR-1 — Common Layer
- FR-1.1 Load all plant/market/solver parameters from config; expose as typed objects.
- FR-1.2 Provide physical plant models (PSP, PV, BESS, reservoir, FCR headroom) usable by every phase.
- FR-1.3 Convert all times correctly: market gates in CET (Europe/Madrid), plant ops in Europe/Lisbon.
- FR-1.4 Persist committed positions per gate to a store; every position read back equals what was written.
- FR-1.5 Write an immutable audit record for every decision (solve, check, approval, submit, reject).
- FR-1.6 Solve every MILP with CPLEX (executable via Pyomo SolverFactory). On solver failure, STOP — never emit an unsolved/feasible-unknown bid.

### FR-2 — Phase 1 Day-Ahead (DA)
- FR-2.1 Load OMIE DA price (PT zone) for delivery date; if real data missing, use clearly-labelled synthetic.
- FR-2.2 Forecast PV production and inflow for all 24 hours.
- FR-2.3 Optimize 24h schedule maximizing expected revenue subject to all physical constraints (§2, §3).
- FR-2.4 Produce one bid per hour (volume MWh, price EUR/MWh), formatted for OMIE.
- FR-2.5 Run permanent bid checker (Phase 3A) before any submission.
- FR-2.6 Run pre-trade risk check (position/volume limits) before any submission.
- FR-2.7 Require trader [A]/[R] approval before submit (unless `--auto-approve`).
- FR-2.8 On approval, submit (stub) and save DA position. On reject, save nothing.

### FR-3 — Phase 2 Intraday (IDA1 D-1 15:00, IDA2 D-1 22:00, IDA3 D 10:00, XBID continuous)
- FR-3.1 Each IDA re-optimizes against the *current committed position* using updated prices/forecasts.
- FR-3.2 IDA only changes a position when the improvement clears the configured threshold
  (`ida_min_delta_mwh`, `ida_min_spread_eur_mwh`); otherwise NO_CHANGE.
- FR-3.3 IDA3 optimizes **hours 12–24 only**. Hours 1–11 are not part of the IDA3 product and must not be re-bid.
- FR-3.4 XBID triggers an order only when spread clears `xbid_min_spread_eur_mwh` and within slippage/volume caps.
- FR-3.5 Every IDA/XBID bid passes the same bid checker and risk check before submission.

### FR-4 — Phase 3 Reserve Offers (aFRR, mFRR)
- FR-4.1 Build an aFRR capacity offer (up/down MW) only from physically available headroom after energy commitments.
- FR-4.2 Build an mFRR capacity offer the same way.
- FR-4.3 Respect aFRR FAT = 5 min and mFRR FAT = 12.5 min when sizing what the plant can actually deliver.
- FR-4.4 Offer volumes never exceed configured market caps (`max_offer_up/dn_mw`, mFRR `max_offer_fraction`).
- FR-4.5 Reserve offer checker (Phase 3A) runs before submission.

### FR-5 — Phase 4 Real-Time Dispatch & Activation
- FR-5.1 Translate committed energy schedule into per-ISP setpoints (PSP units, BESS).
- FR-5.2 On aFRR activation signal, ramp to deliver activated MW within 5 min FAT; log delivered MW per ISP.
- FR-5.3 On mFRR activation signal, deliver within 12.5 min FAT; log delivered MW per ISP.
- FR-5.4 Track actual vs scheduled position per ISP for imbalance settlement.

### FR-6 — Phase 5 Settlement & Analytics
- FR-6.1 DA/IDA settlement = committed volume × final settlement price per market.
- FR-6.2 Reserve settlement = capacity payment + energy activation payment from REN data.
- FR-6.3 Imbalance settlement = (actual − scheduled) × imbalance price (dual pricing).
- FR-6.4 Daily P&L = DA + IDA + XBID + aFRR + mFRR ± imbalance, with per-market breakdown and KPIs.
- FR-6.5 Export an Excel report.

### FR-7 — Phase 6 Backtesting & Validation
- FR-7.1 Replay historical dates through the full pipeline.
- FR-7.2 Validate price/PV forecasts (MAE/RMSE) and MILP solution quality vs realised outcomes.

---

## 2. Prohibitions (what the system MUST NEVER allow) — physical impossibilities

A bid or dispatch that violates any of these must be REJECTED by the checker and never submitted.

- PR-1  A PSP unit must never pump and turbine in the same hour (mutually exclusive modes).
- PR-2  Per-unit turbine output must never exceed 129.6 MW, nor sit strictly between 0 and 57.0 MW
  (no operation below minimum stable load — either off or ≥ 57.0 MW).
- PR-3  Per-unit pump intake must never exceed 111.6 MW.
- PR-4  Total plant generation must never exceed +525.4 MW; total pump demand never beyond −446.4 MW.
- PR-5  Upper reservoir volume must never fall below `min_level_hm3` (830 Mm³) nor exceed usable 3,150 Mm³.
- PR-6  Lower (Pedrógão) reservoir must never fall below `lower_min_hm3` (5 Mm³) nor exceed 54 Mm³.
- PR-7  BESS SOC must never leave [10%, 95%] (0.20 – 1.90 MWh).
- PR-8  BESS must never charge and discharge in the same step.
- PR-9  BESS charge/discharge power must never exceed 1.0 MW.
- PR-10 PV output used in any schedule must never exceed forecast available PV for that hour.
- PR-11 Sum of (energy schedule + reserve capacity offered) must never exceed physical headroom — no MW sold twice.
- PR-12 An aFRR/mFRR offer must never exceed what the plant can deliver within its FAT from current state.
- PR-13 No bid is ever submitted if the MILP did not solve to optimal/feasible — no guessed positions.
- PR-14 No position is ever changed by IDA/XBID below the configured economic threshold (no churn).
- PR-15 Water balance must never be violated: ΔV_upper = inflow − turbine_flow + pump_flow each step (no free water).

---

## 3. Invariants (what must ALWAYS be true at every point in execution)

- INV-1  Energy balance every hour: P_net = P_PSP + P_PV + P_BESS, and P_net equals the bid volume for that hour.
- INV-2  Reservoir continuity: V_upper[t] = V_upper[t-1] + inflow − Q_turbine·Δt + Q_pump·Δt; same for lower (mirror sign).
- INV-3  Water conserved across the pair: water leaving upper enters lower and vice-versa (closed two-reservoir loop, minus inflow/spill).
- INV-4  BESS SOC continuity: SOC[t] = SOC[t-1] + η_c·P_charge·Δt − P_discharge·Δt/η_d, clamped to [10%,95%].
- INV-5  Mode exclusivity flags are binary and sum ≤ 1 per unit per step (off / turbine / pump).
- INV-6  Reserve headroom: for every hour, energy_MW + aFRR_up + mFRR_up ≤ P_max; energy_MW − aFRR_dn − mFRR_dn ≥ P_min.
- INV-7  FCR mandatory non-remunerated headroom is reserved as a constraint and is never offered as a market product (no FCR gate).
- INV-8  Committed position is monotonic in audit: every change has a prior value, a new value, a gate, a timestamp, a reason.
- INV-9  Every submitted bid has passed (bid checker ✓) AND (risk check ✓) AND (approval ✓ or auto-approve) — in that order.
- INV-10 All gate times resolved in CET; all plant/SCADA times in Europe/Lisbon; conversions explicit, never implicit.
- INV-11 IDA3 result set contains only hours 12–24; hours 1–11 are absent, not zero.
- INV-12 Sign convention holds everywhere: generation > 0, pump/charge < 0 — no module flips it silently.

---

## 4. Verification hooks (Phase 3 Step A — built into code, permanent)

Each of the following runs automatically on every output and STOPS on violation:
- `bid_checker` — enforces PR-1..PR-11, PR-13, PR-15, INV-1, INV-5, INV-11 against produced bids.
- `reserve_offer_checker` — enforces PR-11, PR-12, INV-6 against reserve offers.
- `pre_trade_risk_checker` — enforces FR-2.6 position/volume limits.
- `schema_validator` — enforces input data ranges before optimization.
- `physical model asserts` — reservoir/BESS/PSP models assert INV-2..INV-5 internally.

The checker shares this session's blind spots (per protocol). It is a runtime guard,
NOT a substitute for Phase 3 Step B adversarial review in a fresh session.

---

## 5. Out of scope (explicitly not built)

- FCR market gate (mandatory non-remunerated in PT/ES — constraint only).
- Live OMIE/REN/PICASSO API submission (stubbed; real endpoints out of scope for demo).
- Multi-day stochastic reservoir optimization (single 24h horizon per the project title).

---

**Approve this specification to start Phase 2 (Implementation), beginning with the Common Layer.**
