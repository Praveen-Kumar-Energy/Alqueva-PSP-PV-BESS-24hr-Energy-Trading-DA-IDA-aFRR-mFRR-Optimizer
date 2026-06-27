"""
gate_scheduler.py — wall-clock scheduler for the daily market gates.

Resolves every configured gate's pipeline_trigger to the next CET run time and,
in live mode, sleeps until each fires and calls a registered runner. The
scheduler NEVER submits to market itself — a runner queues bids for trader
approval (spec INV-9). Stdlib only; a handful of fixed daily gates does not
justify an external scheduling library.

For the interview demo the gates are normally run by hand, phase by phase
(RUN_PRODUCTION.py). This scheduler is the unattended-operation path and the
`dry_run` view that shows the day's timeline in CET.
"""
from __future__ import annotations

import time
import datetime as dt
from typing import Callable, Dict, List, Tuple

from common_layer.configuration.config_loader import AppConfig, load_config
from common_layer.utilities.logging_utils import get_logger
from common_layer.utilities.timezone_utils import now_market
from common_layer.gate_scheduler.gate_trigger_spec import next_trigger, NextTrigger

log = get_logger(__name__)

# Gates that fire on a fixed daily auction trigger (XBID is continuous — excluded).
SCHEDULED_GATES = ["DA", "IDA1", "IDA2", "IDA3"]

# A runner takes (gate_name, delivery_date) and returns a result dict.
RunnerFn = Callable[[str, dt.date], dict]


class GateScheduler:
    def __init__(self, cfg: AppConfig | None = None):
        self.cfg = cfg or load_config()
        self._runners: Dict[str, RunnerFn] = {}

    def register_runner(self, gate: str, fn: RunnerFn) -> None:
        """Attach the pipeline that runs when `gate` fires."""
        self._runners[gate] = fn

    def upcoming(self, now: dt.datetime | None = None) -> List[Tuple[NextTrigger, str]]:
        """[(NextTrigger, gate)] sorted by run time, for the scheduled gates."""
        out: List[Tuple[NextTrigger, str]] = []
        for gate in SCHEDULED_GATES:
            gate_cfg = self.cfg.market.gates.get(gate)
            if not gate_cfg or not gate_cfg.pipeline_trigger:
                continue
            out.append((next_trigger(gate_cfg.pipeline_trigger, now), gate))
        out.sort(key=lambda x: x[0].run_at_cet)
        return out

    def dry_run(self) -> None:
        """Print the next firing of each gate in CET (no execution)."""
        print("\n  Next gate triggers (CET / Europe-Madrid):")
        for nt, gate in self.upcoming():
            print(f"    {gate:<5} {nt.run_at_cet:%Y-%m-%d %H:%M}  ->  delivery {nt.delivery_date}")
        print()

    def run_forever(self, poll_floor_sec: int = 30) -> None:
        """Sleep until the earliest trigger, run it, repeat.

        Re-resolves after each nap so config edits / clock jumps are picked up
        within poll_floor_sec. A runner exception is logged and does not stop
        the loop (one failed gate must not kill the day)."""
        log.info("Gate scheduler started")
        while True:
            triggers = self.upcoming()
            if not triggers:
                log.error("No triggers configured — exiting")
                return
            nt, gate = triggers[0]
            wait = (nt.run_at_cet - now_market()).total_seconds()
            if wait > 0:
                time.sleep(min(wait, poll_floor_sec))
                continue
            runner = self._runners.get(gate)
            if runner is None:
                log.warning(f"{gate} fired but no runner registered — skipping")
            else:
                try:
                    result = runner(gate, nt.delivery_date)
                    log.info(f"{gate} finished: {result.get('status')}")
                except Exception as exc:                      # keep the day alive
                    log.error(f"{gate} runner raised: {exc}")
            time.sleep(61)        # advance past the trigger minute before re-evaluating
