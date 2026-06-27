"""
ren_isp_signal_loader.py — real-time ISP telemetry / delivery realisation.

During delivery the plant follows its schedule; small unavoidable errors
(forecast deviation, control lag) make actual output differ slightly from the
ISP setpoint. Live mode reads SCADA/REN telemetry; offline this simulates a
realistic actual = scheduled + small mean-reverting noise. The deviation becomes
the imbalance settled in Phase 5C.
"""
from __future__ import annotations

import random
from typing import Dict


def simulate_actual_delivery(scheduled_by_isp: Dict[int, float], delivery_date: str,
                             noise_mw: float = 3.0) -> Dict[int, float]:
    """Actual net per ISP = scheduled + small delivery error (deterministic seed)."""
    rng = random.Random(f"deliv-{delivery_date}")
    actual: Dict[int, float] = {}
    for isp in sorted(scheduled_by_isp):
        actual[isp] = scheduled_by_isp[isp] + rng.uniform(-noise_mw, noise_mw)
    return actual
