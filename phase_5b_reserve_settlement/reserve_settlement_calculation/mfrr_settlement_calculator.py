"""
mfrr_settlement_calculator.py — mFRR settlement (reuses the generic calculator).
"""
from __future__ import annotations

from phase_5b_reserve_settlement.reserve_settlement_calculation.afrr_settlement_calculator import (
    settle_reserve, ReserveSettlement,
)


def settle_mfrr(delivery_date: str, isp_hours: float) -> ReserveSettlement:
    return settle_reserve(delivery_date, "mFRR", isp_hours)
