"""
isp_position_tracker.py — actual vs scheduled position per ISP.

Aggregates the per-ISP scheduled and actual net into the deviation that feeds
imbalance settlement, and reports a delivery KPI (mean absolute deviation).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class DeviationSummary:
    rows: List[dict]                 # [{isp, hour, scheduled_mw, actual_mw, deviation_mw}]
    total_abs_deviation_mwh: float
    mean_abs_deviation_mw: float


def track(scheduled_by_isp: Dict[int, float], actual_by_isp: Dict[int, float],
          isp_to_hour: Dict[int, int], isp_duration_h: float) -> DeviationSummary:
    rows: List[dict] = []
    total_abs = 0.0
    for isp in sorted(scheduled_by_isp):
        sched = scheduled_by_isp[isp]
        act = actual_by_isp.get(isp, sched)
        dev = act - sched
        total_abs += abs(dev) * isp_duration_h
        rows.append({"isp": isp, "hour": isp_to_hour.get(isp, isp),
                     "scheduled_mw": sched, "actual_mw": act, "deviation_mw": dev})
    mad = (sum(abs(r["deviation_mw"]) for r in rows) / len(rows)) if rows else 0.0
    return DeviationSummary(rows=rows, total_abs_deviation_mwh=total_abs,
                            mean_abs_deviation_mw=mad)
