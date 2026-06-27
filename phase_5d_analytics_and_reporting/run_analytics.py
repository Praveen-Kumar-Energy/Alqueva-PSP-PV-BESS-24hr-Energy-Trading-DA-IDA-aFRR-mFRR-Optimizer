"""
run_analytics.py — Phase 5D daily P&L, KPIs, and Excel report.

    python phase_5d_analytics_and_reporting/run_analytics.py --date 2026-06-26
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config, AppConfig
from common_layer.utilities import get_logger, AuditLogger
from common_layer.utilities import date_utils as du
from phase_5d_analytics_and_reporting.analytics_and_kpis.daily_pnl_calculator import (
    compute_daily_pnl,
)
from phase_5d_analytics_and_reporting.analytics_and_kpis.revenue_breakdown_analyzer import (
    revenue_shares,
)
from phase_5d_analytics_and_reporting.analytics_and_kpis.kpi_reporter import compute_kpis
from phase_5d_analytics_and_reporting.daily_excel_reports.daily_report_exporter import (
    export_daily_report,
)
from phase_5c_imbalance_settlement.imbalance_price_and_volume.imbalance_volume_calculator import (
    compute_imbalance,
)

log = get_logger("phase5.analytics")


def run_analytics(delivery_date: str, cfg: AppConfig, export_excel: bool = True) -> dict:
    audit = AuditLogger()
    day = du.parse_date(delivery_date)
    isp_h = du.isp_duration_min(day) / 60.0   # 15 min → 0.25 h (DST days may differ)

    pnl = compute_daily_pnl(delivery_date, cfg)
    shares = revenue_shares(pnl)
    imb_rows = compute_imbalance(delivery_date, isp_h)
    total_imb = sum(abs(r.imbalance_mwh) for r in imb_rows)
    kpis = compute_kpis(delivery_date, pnl, total_imb)

    print("\n" + "=" * 58)
    print(f"  DAILY P&L AND KPIs  —  {delivery_date}")
    print("=" * 58)
    for k, v in pnl.components.items():
        share = shares.get(k, 0.0)
        share_s = f"({share:>4.1f}%)" if k in shares else "       "
        print(f"  {k:<12} {v:>14,.2f} EUR  {share_s}")
    print("  " + "-" * 44)
    print(f"  {'TOTAL P&L':<12} {pnl.total_eur:>14,.2f} EUR")
    print("\n  KPIs:")
    print(f"    Gross energy traded : {kpis.gross_energy_mwh:>10.1f} MWh")
    print(f"    Generation / Pumping: {kpis.generation_mwh:>10.1f} / {kpis.pumping_mwh:.1f} MWh")
    print(f"    Avg sell price      : {kpis.avg_sell_price_eur_mwh:>10.2f} EUR/MWh")
    print(f"    Avg pump price      : {kpis.avg_pump_price_eur_mwh:>10.2f} EUR/MWh")
    print(f"    Spread captured     : {kpis.spread_captured_eur_mwh:>10.2f} EUR/MWh")
    print(f"    Reserve share of P&L: {kpis.reserve_share_pct:>10.1f} %")
    print("=" * 58)

    path = None
    if export_excel:
        try:
            path = export_daily_report(delivery_date)
            print(f"\n  Excel report: {path}")
        except Exception as exc:
            import traceback
            msg = f"Excel export failed: {exc}\n{traceback.format_exc()}"
            log.error(msg)
            print(f"\n  [ERROR] Excel export failed: {exc}", file=__import__("sys").stderr)
            print(traceback.format_exc(), file=__import__("sys").stderr)

    audit.log("ANALYTICS_DONE", total_pnl_eur=pnl.total_eur, report=path)
    return {"status": "OK", "total_pnl_eur": pnl.total_eur,
            "components": pnl.components, "report_path": path}


def main():
    p = argparse.ArgumentParser(description="Run Phase 5D analytics + report")
    p.add_argument("--date", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--no-excel", action="store_true")
    args = p.parse_args()
    r = run_analytics(args.date, load_config(args.config), export_excel=not args.no_excel)
    print("\n  RESULT:", r.get("status"))
    sys.exit(0)


if __name__ == "__main__":
    main()
