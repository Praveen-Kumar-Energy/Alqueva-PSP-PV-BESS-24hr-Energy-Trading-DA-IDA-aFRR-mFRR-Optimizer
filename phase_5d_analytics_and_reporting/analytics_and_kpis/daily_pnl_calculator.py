"""
daily_pnl_calculator.py — consolidate every settlement stream into one daily P&L.

Aggregates the five market settlement components into a single-day profit & loss:

    P&L = DA + IDA(+XBID) + aFRR + mFRR + imbalance

All inputs are read from artefacts produced by phases 5A–5C, so the result is
fully reproducible from stored run output without re-running the optimiser.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from common_layer.configuration.config_loader import AppConfig
from common_layer.utilities import date_utils as du
from phase_5a_da_ida_settlement.energy_settlement_calculation.omie_settlement_price_loader import (
    fetch_settlement_prices,
)
from phase_5a_da_ida_settlement.energy_settlement_calculation.da_settlement_calculator import settle_da
from phase_5a_da_ida_settlement.energy_settlement_calculation.ida_settlement_calculator import (
    settle_intraday,
)
from phase_5b_reserve_settlement.reserve_settlement_calculation.afrr_settlement_calculator import (
    settle_afrr,
)
from phase_5b_reserve_settlement.reserve_settlement_calculation.mfrr_settlement_calculator import (
    settle_mfrr,
)
from phase_5c_imbalance_settlement.imbalance_price_and_volume.ren_imbalance_price_loader import (
    fetch_imbalance_prices,
)
from phase_5c_imbalance_settlement.imbalance_price_and_volume.imbalance_volume_calculator import (
    compute_imbalance,
)
from phase_5c_imbalance_settlement.imbalance_settlement_calculation.imbalance_settlement_calculator import (
    settle_imbalance,
)


@dataclass
class PnLBreakdown:
    components: Dict[str, float] = field(default_factory=dict)

    @property
    def total_eur(self) -> float:
        return sum(self.components.values())


def compute_daily_pnl(delivery_date: str, cfg: AppConfig) -> PnLBreakdown:
    day = du.parse_date(delivery_date)
    hours = du.delivery_hours(day)
    isp_h = du.isp_duration_min(day) / 60.0

    da = settle_da(delivery_date, fetch_settlement_prices(delivery_date, "DA", hours))
    ida = settle_intraday(delivery_date,
                          lambda g, hs: fetch_settlement_prices(delivery_date, g, hs))
    afrr = settle_afrr(delivery_date, isp_h)
    mfrr = settle_mfrr(delivery_date, isp_h)

    short, long_ = fetch_imbalance_prices(delivery_date, hours, cfg)
    imb_rows = compute_imbalance(delivery_date, isp_h)
    imb = settle_imbalance(imb_rows, short, long_)

    return PnLBreakdown(components={
        "DA": da.revenue_eur,
        "IDA+XBID": ida.total_revenue_eur,
        "aFRR": afrr.total_eur,
        "mFRR": mfrr.total_eur,
        "Imbalance": imb.net_eur,
    })
