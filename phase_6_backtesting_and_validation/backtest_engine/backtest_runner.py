"""
backtest_runner.py — replay the optimiser over a span of days.

For each delivery day it:
  * assembles the same DA inputs the live pipeline would,
  * probes MILP solve quality (feasible? objective? checker pass?),
  * scores DA price and PV forecast accuracy vs a synthetic realised series.
Returns per-day rows plus aggregate metrics — the evidence that the model solves
cleanly and the forecasts are reasonable across many days, not just one.

After the loop, compute_risk_metrics() derives VaR(95%/99%), CVaR(95%/99%),
Monte Carlo bootstrap confidence intervals, Sharpe ratio, and max drawdown
from the per-day objective P&L series.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from common_layer.configuration.config_loader import AppConfig
from phase_1_da_day_ahead_bidding.run_da import _assemble_inputs
from phase_6_backtesting_and_validation.backtest_engine.historical_data_loader import (
    date_range, realised_from_forecast,
)
from phase_6_backtesting_and_validation.forecast_and_model_validation.price_forecast_validator import (
    error_metrics,
)
from phase_6_backtesting_and_validation.forecast_and_model_validation.pv_forecast_validator import (
    validate_pv,
)
from phase_6_backtesting_and_validation.forecast_and_model_validation.milp_solution_quality_checker import (
    check_solution_quality,
)
from phase_6_backtesting_and_validation.risk_analytics.portfolio_risk_metrics import (
    RiskMetrics, compute_risk_metrics,
)


@dataclass
class BacktestResult:
    rows: List[dict] = field(default_factory=list)
    n_days: int = 0
    n_feasible: int = 0
    n_checker_pass: int = 0
    avg_objective_eur: float = 0.0
    avg_solve_sec: float = 0.0
    avg_price_mae: float = 0.0
    avg_pv_mae: float = 0.0
    risk: Optional[RiskMetrics] = None   # populated after loop


def run_backtest(start_date: str, n_days: int, cfg: AppConfig) -> BacktestResult:
    res = BacktestResult(n_days=n_days)
    obj_sum = solve_sum = price_mae_sum = pv_mae_sum = 0.0

    for date in date_range(start_date, n_days):
        inputs, _ = _assemble_inputs(date, cfg, use_synthetic=True)
        q = check_solution_quality(inputs, cfg)

        price_actual = realised_from_forecast(inputs["da_prices"], date, 0.10, "px")
        pv_actual = realised_from_forecast(inputs["pv_available_mw"], date, 0.15, "pv")
        pm = error_metrics(inputs["da_prices"], price_actual)
        vm = validate_pv(inputs["pv_available_mw"], pv_actual)

        row = {
            "date": date, "feasible": q.feasible, "checker_pass": q.checker_passed,
            "objective_eur": round(q.objective_eur, 2), "solve_sec": round(q.solve_time_sec, 3),
            "price_mae": round(pm.mae, 2), "price_rmse": round(pm.rmse, 2),
            "pv_mae": round(vm.mae, 4), "note": q.note,
        }
        if q.feasible:
            row["ops"]  = q.operational
            row["tmp"]  = q.temporal
            row["eco"]  = q.economic_ext
        res.rows.append(row)
        res.n_feasible += int(q.feasible)
        res.n_checker_pass += int(q.checker_passed)
        obj_sum += q.objective_eur
        solve_sum += q.solve_time_sec
        price_mae_sum += pm.mae
        pv_mae_sum += vm.mae

    if n_days:
        res.avg_objective_eur = obj_sum / n_days
        res.avg_solve_sec     = solve_sum / n_days
        res.avg_price_mae     = price_mae_sum / n_days
        res.avg_pv_mae        = pv_mae_sum / n_days

    # Risk metrics from the daily objective P&L series (feasible days only).
    pnl_series = [r["objective_eur"] for r in res.rows if r["feasible"]]
    res.risk = compute_risk_metrics(pnl_series)

    return res
