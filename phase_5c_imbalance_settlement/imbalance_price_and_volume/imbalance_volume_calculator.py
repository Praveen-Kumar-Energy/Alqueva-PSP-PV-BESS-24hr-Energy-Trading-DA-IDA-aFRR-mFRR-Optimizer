"""
imbalance_volume_calculator.py — per-ISP imbalance volume.

Imbalance is the UNINSTRUCTED deviation from schedule.

In live mode, actual_mw (from SCADA) includes instructed activations, so they
must be subtracted:
    imbalance_mw = actual - scheduled - instructed_activation

In simulation (Phase 4A), actual_mw = scheduled + noise only — activation
energy is NOT added to actual because Phase 4B/4C run after 4A and are settled
separately through reserve settlement (Phase 5B). Subtracting activations that
were never in actual_mw would create phantom imbalances equal in magnitude to
the activation volume. The correct formula for simulation is therefore:

    imbalance_mw[isp] = actual_mw - scheduled_mw   (= noise only)
    imbalance_mwh      = imbalance_mw * isp_hours

A positive imbalance is LONG (delivered more than owed); negative is SHORT.
Reserve activation energy is fully settled in Phase 5B — not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from common_layer.database import DeliveryStore


@dataclass
class ImbalanceRow:
    isp: int
    hour: int
    imbalance_mwh: float


def compute_imbalance(delivery_date: str, isp_hours: float) -> List[ImbalanceRow]:
    delivery = DeliveryStore().load(delivery_date)
    rows: List[ImbalanceRow] = []
    for d in delivery:
        imb_mw = d["actual_mw"] - d["scheduled_mw"]   # uninstructed noise only
        rows.append(ImbalanceRow(isp=d["isp"], hour=d["hour"],
                                 imbalance_mwh=imb_mw * isp_hours))
    return rows
