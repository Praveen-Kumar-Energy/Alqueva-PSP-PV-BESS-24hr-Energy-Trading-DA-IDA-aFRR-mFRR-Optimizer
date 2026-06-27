"""
run_da.py — Phase 1 Day-Ahead gate orchestrator.

Pipeline (the order is the spec's INV-9 — checks before approval before submit):
    1. load config
    2. assemble inputs (forecast prices / PV / inflow, or OMIE live)
    3. schema-validate inputs
    4. build + solve the shared 24h MILP with CPLEX  (PR-13: stop if unsolved)
    5. extract the schedule
    6. Phase 3A physical bid checker            (stop on any violation)
    7. pre-trade risk checker                   (stop if limits breached)
    8. trader [A]/[R] approval                  (unless --auto-approve)
    9. submit (stub) + save position + audit

Run standalone for the demo:
    python phase_1_da_day_ahead_bidding/run_da.py --date 2026-06-22
    python phase_1_da_day_ahead_bidding/run_da.py --date 2026-06-22 --auto-approve
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

# Allow running this file directly: put the repo root on sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config, AppConfig
from common_layer.utilities import get_logger, AuditLogger
from common_layer.utilities import date_utils as du
from common_layer.utilities.timezone_utils import resolve_gate_time
from common_layer.database import PositionStore, ComponentStore, validate_inputs, SchemaError
from common_layer.optimisation_model import (
    build_core_model, solve_core_model, extract_results, SolveError,
)
from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.omie_da_price_loader import update_training_data
from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.da_price_forecaster import forecast_da_prices
from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.pv_power_forecaster import (
    forecast_pv_available,
)
from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.reservoir_inflow_forecaster import (
    forecast_inflow,
)
from phase_1_da_day_ahead_bidding.da_bid_formatting.da_bid_checker import (
    check_da_bid, BidCheckError,
)
from phase_1_da_day_ahead_bidding.da_bid_formatting.da_bid_formatter import (
    format_da_bids, to_omie_payload, render_table,
)
from phase_1_da_day_ahead_bidding.risk_and_bid_validation.pre_trade_risk_checker import (
    PreTradeRiskChecker,
)
from phase_1_da_day_ahead_bidding.trader_approval.trader_approval_prompt import (
    request_da_approval,
)

log = get_logger("phase1.da")


def _assemble_inputs(delivery_date: str, cfg: AppConfig, use_synthetic: bool) -> tuple[dict, str]:
    """Build the optimisation input bundle. Returns (inputs, price_source)."""
    day   = du.parse_date(delivery_date)
    hours = du.delivery_hours(day)

    # Step 1 — fill Excel with prices up to yesterday.
    # Always runs: real OMIE data when available, synthetic gap-fill on failure.
    # use_synthetic=True only skips live OMIE when internet is unavailable (CI/test).
    if not use_synthetic:
        update_training_data(delivery_date, zone="PT")
    else:
        # Fill any historical gap with synthetic prices so lag features are valid.
        from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.omie_da_price_loader import (
            _fill_synthetic_gap,
        )
        _fill_synthetic_gap(delivery_date, zone="PT")

    # Step 2 — ML forecaster trains on updated history, predicts delivery_date
    da_prices  = forecast_da_prices(hours, delivery_date)
    price_source = "ML_FORECAST"

    pv     = forecast_pv_available(hours, delivery_date, cfg.plant.pv)
    inflow = forecast_inflow(hours, delivery_date, cfg.plant.reservoir)

    inputs = {
        "delivery_date": delivery_date,
        "hours": hours,
        "dt_h": 1.0,
        "da_prices": da_prices,
        "pv_available_mw": pv,
        "inflow_m3h": inflow,
        "initial_state": {
            "upper_reservoir_hm3": cfg.plant.initial_state.upper_reservoir_hm3,
            "lower_reservoir_hm3": cfg.plant.initial_state.lower_reservoir_hm3,
            "bess_soc_frac": cfg.plant.initial_state.bess_soc_frac,
        },
    }
    return inputs, price_source


def run_da(delivery_date: str, config_dir: Optional[str] = None,
           use_synthetic: bool = True, auto_approve: bool = False) -> dict:
    cfg = load_config(config_dir)
    audit = AuditLogger()
    audit.log("DA_START", delivery_date=delivery_date, synthetic=use_synthetic)
    log.info(f"DA gate for delivery {delivery_date} (synthetic={use_synthetic})")

    # 2. inputs --------------------------------------------------------------
    inputs, price_source = _assemble_inputs(delivery_date, cfg, use_synthetic)

    # 3. schema validation ---------------------------------------------------
    try:
        validate_inputs(inputs, cfg)
    except SchemaError as e:
        log.error(str(e))
        audit.log("DA_SCHEMA_FAILED", reason=str(e))
        return {"status": "SCHEMA_FAILED", "reason": str(e)}

    # 4. solve ---------------------------------------------------------------
    try:
        model, meta = build_core_model(inputs, cfg)
        solve_time = solve_core_model(model, cfg, gate="DA")
    except SolveError as e:
        log.error(str(e))
        audit.log("DA_SOLVE_FAILED", reason=str(e))
        return {"status": "SOLVE_FAILED", "reason": str(e)}

    results = extract_results(model, meta)
    log.info(f"Solved in {solve_time:.2f}s | energy revenue "
             f"{results.energy_revenue_eur:,.2f} EUR")
    audit.log("DA_SOLVED", solve_time=solve_time,
              energy_revenue_eur=results.energy_revenue_eur,
              objective_eur=results.objective_eur)

    # 6. Phase 3A physical bid checker --------------------------------------
    try:
        check_da_bid(results, inputs, cfg, gate="DA")
    except BidCheckError as e:
        log.error(str(e))
        audit.log("DA_BIDCHECK_FAILED", reason=str(e))
        return {"status": "BID_CHECK_FAILED", "reason": str(e)}
    audit.log("DA_BIDCHECK_PASSED")

    # 7. risk checker --------------------------------------------------------
    risk = PreTradeRiskChecker(cfg).check(results)
    if not risk.passed:
        log.error("RISK CHECK FAILED: " + "; ".join(risk.violations))
        audit.log("DA_RISK_BLOCKED", violations=risk.violations)
        return {"status": "RISK_BLOCKED", "violations": risk.violations}
    audit.log("DA_RISK_PASSED")

    bids = format_da_bids(results)
    gate_close = resolve_gate_time(cfg.market.gate("DA").gate_close,
                                   du.parse_date(delivery_date)).strftime("%Y-%m-%d %H:%M %Z")

    # 8. approval ------------------------------------------------------------
    if auto_approve:
        log.info("Auto-approve mode -- skipping trader prompt")
        print("\n  DA recommendation (auto-approve):")
        print(render_table(bids))
    else:
        approved = request_da_approval(bids, results, price_source, solve_time, gate_close)
        if not approved:
            log.info("Trader REJECTED DA bids — nothing submitted")
            audit.log("DA_REJECTED")
            return {"status": "REJECTED"}

    # 9. submit (stub) + save ------------------------------------------------
    payload = to_omie_payload(bids, delivery_date)
    ref = f"DA-{delivery_date.replace('-', '')}-001"
    audit.log("DA_SUBMITTED", ref=ref, n_hours=len(bids), n_bids=len(payload["bids"]))
    log.info(f"Submitted (stub). OMIE ref {ref}")

    position = {b.hour: {"volume_mwh": b.volume_mwh, "price_eur_mwh": b.price_eur_mwh}
                for b in bids}
    PositionStore().save_position(delivery_date, "DA", position)
    audit.log("DA_POSITION_SAVED", n_hours=len(position))

    # Save rich component data for analytics Excel report
    ComponentStore().save(
        delivery_date=delivery_date,
        psp_schedule=results.psp_schedule,
        bess_schedule=results.bess_schedule,
        pv_schedule=results.pv_schedule,
        reservoir_trajectory=results.reservoir_trajectory,
        efficiency_per_hour=results.efficiency_per_hour,
        inflow_m3h=inputs["inflow_m3h"],
        solver_metrics={
            "solve_time_sec": round(solve_time, 3),
            "objective_eur": round(results.objective_eur, 2),
            "energy_revenue_eur": round(results.energy_revenue_eur, 2),
            "solver": "CPLEX",
        },
        initial_state=inputs.get("initial_state", {}),
    )

    return {
        "status": "SUBMITTED",
        "ref": ref,
        "price_source": price_source,
        "energy_revenue_eur": results.energy_revenue_eur,
        "objective_eur": results.objective_eur,
        "solve_time_sec": solve_time,
    }


def main():
    p = argparse.ArgumentParser(description="Run the Phase 1 Day-Ahead gate")
    p.add_argument("--date", required=True, help="delivery date YYYY-MM-DD")
    p.add_argument("--config", default=None, help="config dir (default: repo config/)")
    p.add_argument("--auto-approve", action="store_true")
    p.add_argument("--real-data", action="store_true", help="use OMIE live prices")
    args = p.parse_args()

    result = run_da(delivery_date=args.date, config_dir=args.config,
                    use_synthetic=not args.real_data, auto_approve=args.auto_approve)

    print("\n  RESULT:", result.get("status"))
    for k, val in result.items():
        if k != "status":
            print(f"    {k}: {val}")
    sys.exit(0 if result.get("status") == "SUBMITTED" else 1)


if __name__ == "__main__":
    main()
