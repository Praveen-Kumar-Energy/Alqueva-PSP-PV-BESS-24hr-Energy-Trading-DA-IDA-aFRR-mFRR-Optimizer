"""
revenue_breakdown_analyzer.py — per-market revenue share.

Turns the P&L components into shares of total revenue (positive components only,
so a net imbalance cost is shown separately rather than distorting the shares).
"""
from __future__ import annotations

from typing import Dict

from phase_5d_analytics_and_reporting.analytics_and_kpis.daily_pnl_calculator import PnLBreakdown


def revenue_shares(pnl: PnLBreakdown) -> Dict[str, float]:
    positive = {k: v for k, v in pnl.components.items() if v > 0}
    gross = sum(positive.values()) or 1.0
    return {k: round(100.0 * v / gross, 1) for k, v in positive.items()}
