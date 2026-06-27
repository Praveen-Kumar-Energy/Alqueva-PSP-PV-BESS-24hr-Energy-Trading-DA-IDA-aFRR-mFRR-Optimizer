"""
run_afrr_activation.py — Phase 4B aFRR activation during delivery.

Simulates the TSO's AGC activation of the committed aFRR offer, confirms 5-min
FAT deliverability, logs activated energy for settlement. Run aFRR offer (3A)
first.

    python phase_4b_afrr_activation_response/run_afrr_activation.py --date 2026-06-26
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common_layer.configuration import load_config, AppConfig
from common_layer.utilities import get_logger, AuditLogger
from phase_4b_afrr_activation_response.afrr_setpoint_dispatch.afrr_activation_handler import (
    handle_afrr_activation,
)

log = get_logger("phase4.afrr_act")


def run_afrr_activation(delivery_date: str, cfg: AppConfig, no_pause: bool = False) -> dict:
    audit = AuditLogger()
    audit.log("AFRR_ACT_START", delivery_date=delivery_date)
    s = handle_afrr_activation(delivery_date, cfg)
    if s.n_isp_activated == 0:
        log.warning("[aFRR-act] no committed aFRR offer found — run Phase 3A aFRR first.")
        return {"status": "NO_OFFER"}

    print("\n" + "=" * 58)
    print("  aFRR ACTIVATION (delivery)  —  FAT 5 min")
    print("=" * 58)
    print(f"  ISPs activated : {s.n_isp_activated}")
    print(f"  Energy up      : {s.up_mwh:>8.2f} MWh")
    print(f"  Energy down    : {s.dn_mwh:>8.2f} MWh")
    print("=" * 58)
    if not no_pause:
        try:
            input("\n  aFRR activation logged — continue? [ENTER] ")
        except (EOFError, KeyboardInterrupt):
            pass
    audit.log("AFRR_ACT_DONE", n_isp=s.n_isp_activated, up_mwh=s.up_mwh, dn_mwh=s.dn_mwh)
    return {"status": "OK", "n_isp": s.n_isp_activated, "up_mwh": s.up_mwh, "dn_mwh": s.dn_mwh}


def main():
    p = argparse.ArgumentParser(description="Run Phase 4B aFRR activation")
    p.add_argument("--date", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--no-pause", action="store_true")
    args = p.parse_args()
    r = run_afrr_activation(args.date, load_config(args.config), no_pause=args.no_pause)
    print("\n  RESULT:", r.get("status"))
    sys.exit(0 if r.get("status") in ("OK", "NO_OFFER") else 1)


if __name__ == "__main__":
    main()
