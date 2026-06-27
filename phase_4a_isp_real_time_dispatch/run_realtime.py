"""
run_realtime.py — Phase 4A real-time ISP dispatch.

Expands the committed hourly net position into per-ISP setpoints (96 ISPs/day
since 19 Mar 2025), derives concrete PSP unit + BESS setpoints for each hour,
simulates actual delivery, and stores scheduled vs actual per ISP for imbalance
settlement. Run the energy gates first so a committed position exists.

    python phase_4a_isp_real_time_dispatch/run_realtime.py --date 2026-06-26
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config, AppConfig
from common_layer.utilities import get_logger, AuditLogger
from common_layer.utilities import date_utils as du
from common_layer.database import PositionStore, DeliveryStore
from phase_4a_isp_real_time_dispatch.isp_setpoint_dispatch.psp_setpoint_dispatcher import (
    PSPSetpointDispatcher,
)
from phase_4a_isp_real_time_dispatch.isp_setpoint_dispatch.bess_setpoint_dispatcher import (
    BESSSetpointDispatcher,
)
from phase_4a_isp_real_time_dispatch.telemetry.ren_isp_signal_loader import (
    simulate_actual_delivery,
)
from phase_4a_isp_real_time_dispatch.isp_activation_tracking.isp_position_tracker import track

log = get_logger("phase4.realtime")


def run_realtime(delivery_date: str, cfg: AppConfig, no_pause: bool = False) -> dict:
    audit = AuditLogger()
    audit.log("RT_START", delivery_date=delivery_date)

    committed = PositionStore().committed_position(delivery_date)
    if not committed:
        msg = "[RT] no committed position; run DA (and IDAs) first."
        log.error(msg)
        return {"status": "NO_BASELINE", "reason": msg}

    day = du.parse_date(delivery_date)
    isp_h = du.isp_duration_min(day) / 60.0
    psp_disp = PSPSetpointDispatcher(cfg.plant.psp)
    bess_disp = BESSSetpointDispatcher(cfg.plant.bess)

    # Expand hourly net to ISP setpoints; derive PSP+BESS setpoints per hour.
    scheduled_by_isp = {}
    isp_to_hour = {}
    soc = cfg.plant.bess.initial_soc_frac * cfg.plant.bess.capacity_mwh
    violations = []
    for h in sorted(committed):
        net = committed[h]
        # PSP covers the bulk; BESS trims the residual (fast response, SOC-limited).
        units = psp_disp.allocate(net)
        violations += psp_disp.validate(units, label=f"H{h}")
        psp_net = PSPSetpointDispatcher.net_mw(units)
        residual = net - psp_net
        _, soc = bess_disp.setpoint(residual, soc, 1.0)  # dt=1h: setpoint called once per hour
        for isp in du.hour_to_isps(h, day):
            scheduled_by_isp[isp] = net
            isp_to_hour[isp] = h

    if violations:
        log.warning(f"[RT] {len(violations)} setpoint feasibility notes (target from net only)")

    actual_by_isp = simulate_actual_delivery(scheduled_by_isp, delivery_date)
    summary = track(scheduled_by_isp, actual_by_isp, isp_to_hour, isp_h)

    DeliveryStore().save(delivery_date, summary.rows)
    audit.log("RT_DELIVERED", n_isp=len(summary.rows),
              total_abs_deviation_mwh=summary.total_abs_deviation_mwh)

    _print_summary(delivery_date, cfg, summary)
    if not no_pause:
        try:
            input("\n  Real-time delivery simulated — continue? [ENTER] ")
        except (EOFError, KeyboardInterrupt):
            pass

    log.info(f"RT done: {len(summary.rows)} ISPs, "
             f"MAD {summary.mean_abs_deviation_mw:.2f} MW")
    return {"status": "OK", "n_isp": len(summary.rows),
            "total_abs_deviation_mwh": summary.total_abs_deviation_mwh,
            "mean_abs_deviation_mw": summary.mean_abs_deviation_mw}


def _print_summary(delivery_date, cfg, summary):
    print("\n" + "=" * 60)
    print(f"  REAL-TIME ISP DISPATCH  —  {delivery_date}")
    print("=" * 60)
    _day = du.parse_date(delivery_date)
    print(f"  ISP length    : {du.isp_duration_min(_day)} min "
          f"({len(summary.rows)} ISPs)")
    print(f"  Mean abs dev  : {summary.mean_abs_deviation_mw:.2f} MW")
    print(f"  Total abs dev : {summary.total_abs_deviation_mwh:.2f} MWh "
          f"(-> imbalance settlement)")
    print("  First 4 ISPs:")
    print(f"  {'ISP':<5} {'Hour':<5} {'Sched MW':>10} {'Actual MW':>10} {'Dev MW':>8}")
    for r in summary.rows[:4]:
        print(f"  {r['isp']:<5} H{r['hour']:02d}  {r['scheduled_mw']:>+10.1f} "
              f"{r['actual_mw']:>+10.1f} {r['deviation_mw']:>+8.2f}")
    print("=" * 60)


def main():
    p = argparse.ArgumentParser(description="Run Phase 4A real-time ISP dispatch")
    p.add_argument("--date", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--no-pause", action="store_true")
    args = p.parse_args()
    result = run_realtime(args.date, load_config(args.config), no_pause=args.no_pause)
    print("\n  RESULT:", result.get("status"))
    sys.exit(0 if result.get("status") == "OK" else 1)


if __name__ == "__main__":
    main()
