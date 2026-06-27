"""
pre_trade_risk_checker.py — commercial/risk gate before submission (spec FR-2.6).

Separate from the physical bid checker: that one asks "is this dispatch
physically possible?"; this one asks "is this position within the limits the
desk is allowed to take?". Checks:
    * per-hour net within the contractual position limits,
    * total day volume within a configured cap,
    * expected revenue not absurd (sign/scale sanity — catches a runaway model).

Returns a RiskResult; the runner blocks submission if not passed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model.core_milp_solver import GateResults


@dataclass
class RiskResult:
    passed: bool
    violations: List[str] = field(default_factory=list)


class PreTradeRiskChecker:
    def __init__(self, cfg: AppConfig, max_day_volume_mwh: float = 15000.0,
                 max_revenue_eur: float = 5_000_000.0):
        self.cfg = cfg
        self.max_day_volume_mwh = max_day_volume_mwh
        self.max_revenue_eur = max_revenue_eur

    def check(self, results: GateResults) -> RiskResult:
        v: List[str] = []
        bl = self.cfg.market.bid_limits

        total_abs = 0.0
        for h, net in results.net_position_mw.items():
            if net > bl.max_generation_mw + 1e-6:
                v.append(f"H{h} net {net:.2f} MW exceeds position limit "
                         f"{bl.max_generation_mw} MW (sell)")
            if net < -bl.max_pump_mw - 1e-6:
                v.append(f"H{h} net {net:.2f} MW exceeds position limit "
                         f"{-bl.max_pump_mw} MW (buy)")
            total_abs += abs(net)

        if total_abs > self.max_day_volume_mwh:
            v.append(f"day gross volume {total_abs:.0f} MWh exceeds cap "
                     f"{self.max_day_volume_mwh:.0f} MWh")

        if abs(results.energy_revenue_eur) > self.max_revenue_eur:
            v.append(f"expected revenue {results.energy_revenue_eur:,.0f} EUR exceeds "
                     f"sanity cap {self.max_revenue_eur:,.0f} EUR — review model inputs")

        return RiskResult(passed=not v, violations=v)
