"""
component_store.py — persist per-component DA dispatch results to JSON.

Saves the rich GateResults decomposition (per-unit PSP, BESS, PV, reservoir,
efficiency, water flows) that the MILP computes but PositionStore discards.
Also stores natural inflow and solver metrics for the analytics Excel exporter.

File: runtime/components/components_<date>.json
"""
from __future__ import annotations

import json
import os
from typing import Dict, Any, Optional


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


def _path(delivery_date: str) -> str:
    d = os.path.join(_repo_root(), "runtime", "components")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"components_{delivery_date}.json")


class ComponentStore:
    """Save and load per-component hourly dispatch data for a delivery date."""

    def save(
        self,
        delivery_date: str,
        psp_schedule: Dict[int, dict],
        bess_schedule: Dict[int, dict],
        pv_schedule: Dict[int, dict],
        reservoir_trajectory: Dict[int, dict],
        efficiency_per_hour: Dict[int, dict],
        inflow_m3h: Dict[int, float],
        solver_metrics: Optional[dict] = None,
        initial_state: Optional[dict] = None,
    ) -> None:
        payload = {
            "delivery_date": delivery_date,
            "psp_schedule":          {str(h): v for h, v in psp_schedule.items()},
            "bess_schedule":         {str(h): v for h, v in bess_schedule.items()},
            "pv_schedule":           {str(h): v for h, v in pv_schedule.items()},
            "reservoir_trajectory":  {str(h): v for h, v in reservoir_trajectory.items()},
            "efficiency_per_hour":   {str(h): v for h, v in efficiency_per_hour.items()},
            "inflow_m3h":            {str(h): v for h, v in inflow_m3h.items()},
            "solver_metrics":        solver_metrics or {},
            "initial_state":         initial_state or {},
        }
        with open(_path(delivery_date), "w") as f:
            json.dump(payload, f, indent=2)

    def load(self, delivery_date: str) -> Optional[dict]:
        p = _path(delivery_date)
        if not os.path.exists(p):
            return None
        with open(p) as f:
            raw = json.load(f)
        # Re-key hour strings back to int
        for key in ("psp_schedule", "bess_schedule", "pv_schedule",
                    "reservoir_trajectory", "efficiency_per_hour", "inflow_m3h"):
            if key in raw:
                raw[key] = {int(h): v for h, v in raw[key].items()}
        return raw

    def load_initial_state(self, delivery_date: str) -> dict:
        """Return the initial_state dict saved with this date, or empty dict."""
        raw = self.load(delivery_date)
        return raw.get("initial_state", {}) if raw else {}
