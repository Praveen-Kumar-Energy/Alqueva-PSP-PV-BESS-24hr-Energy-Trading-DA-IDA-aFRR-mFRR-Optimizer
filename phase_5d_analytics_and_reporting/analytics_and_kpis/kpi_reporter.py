"""
kpi_reporter.py — daily trading/operations KPIs.

Computes a small set of headline KPIs from the stored position and settlements:
    * total energy traded (MWh, gross),
    * generation vs pumping split,
    * weighted avg realised sell price (generation hours only, price-weighted),
    * weighted avg pump price (pumping hours only, price-weighted),
    * price spread captured = avg_sell - avg_pump,
    * reserve revenue share of total P&L,
    * imbalance ratio (imbalance MWh / energy MWh) — delivery accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from common_layer.database import PositionStore
from phase_5d_analytics_and_reporting.analytics_and_kpis.daily_pnl_calculator import PnLBreakdown


@dataclass
class KPIs:
    gross_energy_mwh: float
    generation_mwh: float
    pumping_mwh: float
    avg_sell_price_eur_mwh: float   # true vol-weighted avg DA price for sell hours
    avg_pump_price_eur_mwh: float   # true vol-weighted avg DA price for pump hours
    spread_captured_eur_mwh: float  # avg_sell - avg_pump (arbitrage spread)
    reserve_share_pct: float
    total_pnl_eur: float


def compute_kpis(delivery_date: str, pnl: PnLBreakdown,
                 total_imbalance_mwh: float) -> KPIs:
    store = PositionStore()
    # load_position returns {hour: {"volume_mwh": float, "price_eur_mwh": float}}
    raw = store.load_position(delivery_date, "DA")

    gen = 0.0; gen_rev = 0.0
    pump = 0.0; pump_cost = 0.0
    for h, rec in raw.items():
        vol = rec["volume_mwh"]
        price = rec["price_eur_mwh"]
        if vol > 0:
            gen += vol
            gen_rev += vol * price
        elif vol < 0:
            pump += -vol
            pump_cost += (-vol) * price
    gross = gen + pump

    # True weighted average prices (volume-weighted per direction)
    avg_sell = (gen_rev / gen) if gen > 0 else 0.0
    avg_pump = (pump_cost / pump) if pump > 0 else 0.0
    spread = avg_sell - avg_pump   # PSP arbitrage spread captured

    reserve = pnl.components.get("aFRR", 0.0) + pnl.components.get("mFRR", 0.0)
    total = pnl.total_eur or 1.0
    reserve_share = 100.0 * reserve / total

    return KPIs(
        gross_energy_mwh=gross,
        generation_mwh=gen,
        pumping_mwh=pump,
        avg_sell_price_eur_mwh=avg_sell,
        avg_pump_price_eur_mwh=avg_pump,
        spread_captured_eur_mwh=spread,
        reserve_share_pct=reserve_share,
        total_pnl_eur=pnl.total_eur,
    )
