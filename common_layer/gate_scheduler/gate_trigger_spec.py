"""
gate_trigger_spec.py — resolve a gate's trigger spec to the next CET run time.

Builds on timezone_utils.resolve_gate_time. Given "D-1 11:00" and "now", finds
the next wall-clock CET datetime that fires that gate, plus the delivery day it
serves. Searches forward over candidate delivery days so it works for any offset.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from common_layer.utilities.timezone_utils import (
    resolve_gate_time, now_market, MARKET_TZ,
)


@dataclass(frozen=True)
class NextTrigger:
    run_at_cet: dt.datetime
    delivery_date: dt.date


def next_trigger(spec: str, now: dt.datetime | None = None) -> NextTrigger:
    """Next CET firing of `spec` and the delivery day it serves.

    Candidate delivery days: today..today+3 covers every supported offset
    (D, D-1, D-2, D+1). Returns the first whose run time is still in the future."""
    if now is None:
        now = now_market()
    if now.tzinfo is None:
        now = now.replace(tzinfo=MARKET_TZ)

    for delta in range(0, 4):
        delivery = now.date() + dt.timedelta(days=delta)
        run_at = resolve_gate_time(spec, delivery)
        if run_at > now:
            return NextTrigger(run_at, delivery)
    raise RuntimeError(f"Could not resolve next trigger for spec {spec!r}")
