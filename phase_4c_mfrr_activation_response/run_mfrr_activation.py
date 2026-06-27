"""
run_mfrr_activation.py — Phase 4C mFRR activation during delivery.

Simulates the TSO instruction to activate the committed mFRR offer, confirms
12.5-min FAT deliverability, logs activated energy for settlement. Run mFRR offer
(3B) first.

    python phase_4c_mfrr_activation_response/run_mfrr_activation.py --date 2026-06-26
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config, AppConfig
from common_layer.utilities import get_logger, AuditLogger
from phase_4c_mfrr_activation_response.mfrr_setpoint_dispatch.mfrr_activation_handler import (
    handle_mfrr_activation,
)

log = get_logger("phase4.mfrr_act")


def run_mfrr_activation(delivery_date: str, cfg: AppConfig, no_pause: bool = False) -> dict:
    audit = AuditLogger()
    audit.log("MFRR_ACT_START", delivery_date=delivery_date)
    s = handle_mfrr_activation(delivery_date, cfg)
    if s.n_isp_activated == 0:
        log.warning("[mFRR-act] no committed mFRR offer found — run Phase 3B mFRR first.")
        return {"status": "NO_OFFER"}

    print("\n" + "=" * 58)
    print("  mFRR ACTIVATION (delivery)  —  FAT 12.5 min")
    print("=" * 58)
    print(f"  ISPs activated : {s.n_isp_activated}")
    print(f"  Energy up      : {s.up_mwh:>8.2f} MWh")
    print(f"  Energy down    : {s.dn_mwh:>8.2f} MWh")
    print("=" * 58)
    if not no_pause:
        try:
            input("\n  mFRR activation logged — continue? [ENTER] ")
        except (EOFError, KeyboardInterrupt):
            pass
    audit.log("MFRR_ACT_DONE", n_isp=s.n_isp_activated, up_mwh=s.up_mwh, dn_mwh=s.dn_mwh)
    return {"status": "OK", "n_isp": s.n_isp_activated, "up_mwh": s.up_mwh, "dn_mwh": s.dn_mwh}


def main():
    p = argparse.ArgumentParser(description="Run Phase 4C mFRR activation")
    p.add_argument("--date", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--no-pause", action="store_true")
    args = p.parse_args()
    r = run_mfrr_activation(args.date, load_config(args.config), no_pause=args.no_pause)
    print("\n  RESULT:", r.get("status"))
    sys.exit(0 if r.get("status") in ("OK", "NO_OFFER") else 1)


if __name__ == "__main__":
    main()
