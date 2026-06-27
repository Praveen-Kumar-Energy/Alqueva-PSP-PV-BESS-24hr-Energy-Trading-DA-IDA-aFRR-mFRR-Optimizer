"""
ida_reoptimiser.py — shared intraday re-optimisation engine for IDA1/2/3.

All three intraday auctions do the same thing; they differ only in:
  * which gate's schedule is the baseline (DA -> IDA1 -> IDA2 -> IDA3),
  * the tradable hour window (IDA3 = hours 12-24 ONLY; hours 1-11 are frozen),
  * the updated intraday price curve (new information closer to delivery).

The engine:
  1. loads the committed baseline (the previous gate's net schedule),
  2. builds intraday inputs (updated prices, PV nowcast, inflow),
  3. freezes every hour OUTSIDE the tradable window to the committed net (INV-11),
  4. re-solves the shared 24h MILP under the new prices,
  5. applies the no-churn threshold (PR-14): if the re-optimised schedule does not
     beat holding the committed position by the configured volume AND spread, it
     returns NO_CHANGE and submits nothing,
  6. otherwise runs the Phase 3A physical checker + risk check, pauses for the
     operator (ENTER), submits (stub), and saves the new committed position.

Submitting the whole re-optimised schedule (not patched per hour) keeps the
position a physically consistent optimum; the per-hour deltas are the IDA trades.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from common_layer.configuration.config_loader import AppConfig
from common_layer.utilities import get_logger, AuditLogger
from common_layer.utilities import date_utils as du
from common_layer.database import PositionStore, validate_inputs, SchemaError
from common_layer.optimisation_model.core_milp_builder import build_core_model
from common_layer.optimisation_model.core_milp_solver import (
    solve_core_model, extract_results, SolveError,
)
from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.da_price_forecaster import (
    forecast_da_prices,
)
from phase_2a_ida1_intraday_auction_1.ida1_price_forecasting.ida1_price_forecaster import (
    forecast_ida1_prices,
)
from phase_2b_ida2_intraday_auction_2.ida2_price_forecasting.ida2_price_forecaster import (
    forecast_ida2_prices,
)
from phase_2c_ida3_intraday_auction_3.ida3_price_forecasting.ida3_price_forecaster import (
    forecast_ida3_prices,
)
from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.pv_power_forecaster import (
    forecast_pv_available,
)
from phase_1_da_day_ahead_bidding.da_price_pv_inflow_forecasting.reservoir_inflow_forecaster import (
    forecast_inflow,
)
from phase_1_da_day_ahead_bidding.da_bid_formatting.da_bid_checker import (
    check_da_bid, BidCheckError,
)
from phase_1_da_day_ahead_bidding.risk_and_bid_validation.pre_trade_risk_checker import (
    PreTradeRiskChecker,
)

log = get_logger("phase2.ida")

# Which gate's committed schedule each IDA starts from.
_BASELINE_GATE = {"IDA1": "DA", "IDA2": "IDA1", "IDA3": "IDA2"}


def _get_intraday_prices(hours: List[int], delivery_date: str,
                         gate: str) -> Dict[int, float]:
    """Gate-specific intraday price forecast = DA forecast + ML-predicted spread.

    Each gate has its own dedicated model trained on that gate's historical
    SIDC clearing prices. DA prices computed first, spread added on top.
    Falls back to DA prices if forecaster fails.
    """
    da_prices = forecast_da_prices(hours, delivery_date)
    if gate == "IDA1":
        return forecast_ida1_prices(hours, delivery_date, da_prices)
    elif gate == "IDA2":
        return forecast_ida2_prices(hours, delivery_date, da_prices)
    else:
        return forecast_ida3_prices(hours, delivery_date, da_prices)


def _build_inputs(gate: str, delivery_date: str, cfg: AppConfig) -> dict:
    day = du.parse_date(delivery_date)
    hours = du.delivery_hours(day)
    return {
        "delivery_date": delivery_date,
        "hours": hours,
        "dt_h": 1.0,
        "da_prices": _get_intraday_prices(hours, delivery_date, gate),
        "pv_available_mw": forecast_pv_available(hours, delivery_date, cfg.plant.pv),
        "inflow_m3h": forecast_inflow(hours, delivery_date, cfg.plant.reservoir),
        "initial_state": {
            "upper_reservoir_hm3": cfg.plant.initial_state.upper_reservoir_hm3,
            "lower_reservoir_hm3": cfg.plant.initial_state.lower_reservoir_hm3,
            "bess_soc_frac": cfg.plant.initial_state.bess_soc_frac,
        },
    }


def _pause(message: str, no_pause: bool) -> None:
    """Operator pause for the demo (ENTER to continue). Skipped if no_pause."""
    if no_pause:
        return
    try:
        input(f"\n  {message}  [ENTER to continue] ")
    except (EOFError, KeyboardInterrupt):
        pass


def reoptimise_ida(gate: str, delivery_date: str, cfg: AppConfig,
                   no_pause: bool = False) -> dict:
    """Run one IDA gate. Returns a status dict."""
    audit = AuditLogger()
    audit.log(f"{gate}_START", delivery_date=delivery_date)
    gate_cfg = cfg.market.gate(gate)
    store = PositionStore()
    dt = 1.0

    # 1. committed baseline (previous gate's running net). ------------------
    baseline_gate = _BASELINE_GATE[gate]
    committed = store.committed_position(delivery_date, as_of_gate=baseline_gate)
    if not committed:
        msg = (f"[{gate}] no committed baseline from {baseline_gate}; "
               f"run the earlier gate(s) first.")
        log.error(msg)
        return {"status": "NO_BASELINE", "reason": msg}

    # 2. intraday inputs ----------------------------------------------------
    inputs = _build_inputs(gate, delivery_date, cfg)
    hours = inputs["hours"]
    try:
        validate_inputs(inputs, cfg)
    except SchemaError as e:
        audit.log(f"{gate}_SCHEMA_FAILED", reason=str(e))
        return {"status": "SCHEMA_FAILED", "reason": str(e)}

    # 3. freeze hours outside the tradable window (IDA3 -> freeze 1-11). ----
    tradable = [h for h in hours if gate_cfg.hour_in_product(h)]
    fixed_net = {h: committed.get(h, 0.0) for h in hours if h not in tradable}
    log.info(f"{gate}: tradable hours {tradable[0]}-{tradable[-1]} "
             f"({len(tradable)} of {len(hours)}); {len(fixed_net)} frozen")

    # 4. re-solve under intraday prices -------------------------------------
    try:
        model, meta = build_core_model(inputs, cfg, fixed_net_position=fixed_net)
        solve_time = solve_core_model(model, cfg, gate=gate)
    except SolveError as e:
        audit.log(f"{gate}_SOLVE_FAILED", reason=str(e))
        return {"status": "SOLVE_FAILED", "reason": str(e)}
    results = extract_results(model, meta)
    new_net = results.net_position_mw
    price = inputs["da_prices"]

    # 5. no-churn threshold (PR-14). ----------------------------------------
    # Decision: re-bid only if the re-optimised schedule's expected energy value
    # (under intraday prices, tradable hours) beats holding the committed position
    # by at least a DYNAMIC threshold, and at least one hour moves by more than
    # the volume noise floor. one_way_vol halves the summed |delta| so a pure swap
    # (sell hour A / buy hour B) is counted once, not twice.
    #
    # Dynamic threshold: max(floor, pct% of |DA_position_value| in tradable hours).
    # This auto-scales to the plant's actual market exposure — a large committed
    # position at high prices warrants a higher absolute improvement bar, preventing
    # microstructure noise from triggering unnecessary re-bids.
    th = cfg.market.trading_thresholds
    deltas = {h: (new_net[h] - committed.get(h, 0.0)) for h in tradable}
    one_way_vol = 0.5 * sum(abs(d) * dt for d in deltas.values())
    committed_value = sum(price[h] * committed.get(h, 0.0) * dt for h in tradable)
    new_value = sum(price[h] * new_net[h] * dt for h in tradable)
    improvement = new_value - committed_value

    # Dynamic threshold: 0.15% of the absolute DA position value in tradable hours
    da_value_tradable = sum(abs(price[h] * committed.get(h, 0.0)) for h in tradable)
    dynamic_threshold = max(
        th.ida_min_rebid_eur_floor,
        (th.ida_min_rebid_pct / 100.0) * da_value_tradable,
    )
    log.info(f"{gate}: dynamic threshold = max(floor {th.ida_min_rebid_eur_floor:.0f} EUR, "
             f"{th.ida_min_rebid_pct:.2f}% × {da_value_tradable:,.0f} EUR) "
             f"= {dynamic_threshold:,.0f} EUR")

    material = {h: d for h, d in deltas.items() if abs(d * dt) >= th.ida_min_delta_mwh}

    if not material or improvement < dynamic_threshold:
        log.info(f"{gate}: NO_CHANGE — repositioning {one_way_vol:.1f} MWh, "
                 f"gain {improvement:,.0f} EUR < dynamic threshold {dynamic_threshold:,.0f} EUR")
        audit.log(f"{gate}_NO_CHANGE", one_way_vol_mwh=one_way_vol,
                  improvement_eur=improvement, dynamic_threshold_eur=dynamic_threshold,
                  da_value_tradable_eur=da_value_tradable)
        _print_ida_summary(gate, price, committed, new_net, tradable, deltas,
                           improvement, decision="NO_CHANGE",
                           dynamic_threshold=dynamic_threshold)
        _pause(f"{gate}: no material improvement — holding committed position.", no_pause)
        return {"status": "NO_CHANGE", "improvement_eur": improvement,
                "one_way_vol_mwh": one_way_vol,
                "dynamic_threshold_eur": dynamic_threshold}

    # 6. Phase 3A physical checker + risk -----------------------------------
    try:
        check_da_bid(results, inputs, cfg, gate=gate)
    except BidCheckError as e:
        audit.log(f"{gate}_BIDCHECK_FAILED", reason=str(e))
        return {"status": "BID_CHECK_FAILED", "reason": str(e)}
    risk = PreTradeRiskChecker(cfg).check(results)
    if not risk.passed:
        audit.log(f"{gate}_RISK_BLOCKED", violations=risk.violations)
        return {"status": "RISK_BLOCKED", "violations": risk.violations}

    _print_ida_summary(gate, price, committed, new_net, tradable, deltas,
                       improvement, decision="RE-BID",
                       dynamic_threshold=dynamic_threshold)
    _pause(f"{gate}: re-optimised, +{improvement:,.0f} EUR — about to submit.", no_pause)

    # 7. submit (stub) + save new committed position ------------------------
    ref = f"{gate}-{delivery_date.replace('-', '')}-001"
    position = {h: {"volume_mwh": new_net[h] * dt, "price_eur_mwh": price[h]}
                for h in hours if gate_cfg.hour_in_product(h)}
    store.save_position(delivery_date, gate, position)
    audit.log(f"{gate}_SUBMITTED", ref=ref, improvement_eur=improvement,
              n_hours=len(position), dynamic_threshold_eur=dynamic_threshold,
              da_value_tradable_eur=da_value_tradable)
    log.info(f"{gate} submitted (stub) ref {ref}; saved {len(position)} hours")
    return {"status": "SUBMITTED", "ref": ref, "improvement_eur": improvement,
            "one_way_vol_mwh": one_way_vol, "solve_time_sec": solve_time,
            "committed_net_mw": dict(committed),
            "new_net_mw": {h: float(new_net[h]) for h in tradable},
            "ida_prices": dict(price),
            "tradable_hours": tradable,
            "dynamic_threshold_eur": dynamic_threshold,
            "da_value_tradable_eur": da_value_tradable}


def _print_ida_summary(gate: str, price: Dict[int, float], committed: Dict[int, float],
                       new_net: Dict[int, float], tradable: List[int],
                       deltas: Dict[int, float], improvement: float, decision: str,
                       dynamic_threshold: Optional[float] = None) -> None:
    print("\n" + "=" * 62)
    print(f"  {gate} RE-OPTIMISATION  —  decision: {decision}")
    print("=" * 62)
    print(f"  Tradable hours : {tradable[0]}-{tradable[-1]}")
    print(f"  Expected improvement vs committed: {improvement:>12,.2f} EUR")
    if dynamic_threshold is not None:
        status = "PASS" if improvement >= dynamic_threshold else "FAIL"
        print(f"  Dynamic re-bid threshold       : {dynamic_threshold:>12,.2f} EUR  [{status}]")
    print(f"\n  {'Hour':<5} {'Price':>7} {'Committed':>11} {'New':>9} {'Delta MWh':>11}")
    print("  " + "-" * 48)
    for h in tradable:
        d = deltas[h]
        mark = "  <-- trade" if abs(d) >= 0.5 else ""
        print(f"  H{h:02d}  {price[h]:>7.1f} {committed.get(h,0.0):>+11.1f} "
              f"{new_net[h]:>+9.1f} {d:>+11.2f}{mark}")
    print("=" * 62)
