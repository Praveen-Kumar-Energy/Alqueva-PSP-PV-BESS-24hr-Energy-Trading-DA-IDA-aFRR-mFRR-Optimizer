"""
run_backtest.py — Phase 6 backtest over a span of delivery days.

    python phase_6_backtesting_and_validation/run_backtest.py --start 2026-06-01 --days 7
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config
from common_layer.utilities import get_logger, AuditLogger
from phase_6_backtesting_and_validation.backtest_engine.backtest_runner import run_backtest
from phase_6_backtesting_and_validation.backtest_excel_reports.backtest_report_exporter import (
    export_backtest,
)

log = get_logger("phase6.backtest")


def main():
    p = argparse.ArgumentParser(description="Run Phase 6 backtest")
    p.add_argument("--start", required=True, help="start delivery date YYYY-MM-DD")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--config", default=None)
    p.add_argument("--no-excel", action="store_true")
    args = p.parse_args()

    cfg = load_config(args.config)
    audit = AuditLogger()
    res = run_backtest(args.start, args.days, cfg)

    print("\n" + "=" * 64)
    print(f"  BACKTEST  —  {args.start}  x {args.days} days")
    print("=" * 64)
    print(f"  {'Date':<12} {'Feas':>5} {'Chk':>4} {'Objective':>12} "
          f"{'Solve s':>8} {'PxMAE':>7} {'PVMAE':>7}")
    print("  " + "-" * 60)
    for r in res.rows:
        print(f"  {r['date']:<12} {str(r['feasible']):>5} {str(r['checker_pass']):>4} "
              f"{r['objective_eur']:>12,.0f} {r['solve_sec']:>8.3f} "
              f"{r['price_mae']:>7.2f} {r['pv_mae']:>7.3f}")
    print("  " + "-" * 60)
    print(f"  Feasible: {res.n_feasible}/{res.n_days}   "
          f"Checker pass: {res.n_checker_pass}/{res.n_days}")
    print(f"  Avg objective: {res.avg_objective_eur:,.0f} EUR   "
          f"Avg solve: {res.avg_solve_sec:.3f} s")
    print(f"  Avg price MAE: {res.avg_price_mae:.2f} EUR/MWh   "
          f"Avg PV MAE: {res.avg_pv_mae:.4f} MW")
    print("=" * 64)

    if res.risk is not None:
        rm = res.risk
        print("\n  PORTFOLIO RISK METRICS")
        print("  " + "-" * 62)
        print(f"  {'Mean daily P&L':<38} {rm.mean_pnl_eur:>14,.0f} EUR")
        print(f"  {'Std daily P&L':<38} {rm.std_pnl_eur:>14,.0f} EUR")
        print(f"  {'Min / Max daily P&L':<38} {rm.min_pnl_eur:>14,.0f} / "
              f"{rm.max_pnl_eur:,.0f} EUR")
        print()
        print(f"  {'VaR(95%)  historical [5th pct]':<38} {rm.var_95_eur:>14,.0f} EUR")
        print(f"  {'CVaR(95%) Expected Shortfall':<38} {rm.cvar_95_eur:>14,.0f} EUR")
        print(f"  {'VaR(99%)  historical [1st pct]':<38} {rm.var_99_eur:>14,.0f} EUR")
        print(f"  {'CVaR(99%) Expected Shortfall':<38} {rm.cvar_99_eur:>14,.0f} EUR")
        print()
        print(f"  Monte Carlo bootstrap (n=10,000, alpha=95%)")
        print(f"  {'  VaR(95%)  mean ± std':<38} {rm.var_95_mean:>14,.0f} ± "
              f"{rm.var_95_std:,.0f} EUR")
        print(f"  {'  CVaR(95%) mean ± std':<38} {rm.cvar_95_mean:>14,.0f} ± "
              f"{rm.cvar_95_std:,.0f} EUR")
        print()
        print(f"  {'Sharpe ratio (annualised, rf=0)':<38} {rm.sharpe_ratio:>14.4f}")
        print(f"  {'Max drawdown':<38} {rm.max_drawdown_eur:>14,.0f} EUR")
        print("  " + "-" * 62)

    if not args.no_excel:
        try:
            path = export_backtest(args.start, res)
            print(f"\n  Excel report: {path}")
        except Exception as exc:
            log.error(f"Excel export failed: {exc}")

    audit.log("BACKTEST_DONE", days=res.n_days, feasible=res.n_feasible,
              checker_pass=res.n_checker_pass)
    sys.exit(0 if res.n_feasible == res.n_days == res.n_checker_pass else 1)


if __name__ == "__main__":
    main()
