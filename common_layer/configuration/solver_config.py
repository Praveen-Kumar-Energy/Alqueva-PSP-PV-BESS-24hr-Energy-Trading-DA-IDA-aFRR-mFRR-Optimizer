"""
solver_config.py — typed CPLEX/solver settings.

Loads config/solver.yaml. Primary solver is the CPLEX command-line executable,
invoked via Pyomo SolverFactory(name, executable=path). Using the executable
rather than the CPLEX Python binding avoids ABI conflicts when the binding was
compiled for a different Python version.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class SolverConfig:
    name: str
    executable: str
    fallback_order: List[str]
    mip_gap: float
    threads: int
    time_limit_sec: Dict[str, int]

    def time_limit_for(self, gate: str) -> int:
        """Return the time limit for a gate, falling back to the default."""
        return int(self.time_limit_sec.get(gate, self.time_limit_sec.get("default", 120)))

    def resolve_executable(self) -> Optional[str]:
        """Return a usable CPLEX executable path, or None if not found.

        Search order: explicit config path first, then PATH lookup. Callers
        must abort if this returns None — never bid on an unsolved model (PR-13)."""
        if self.executable and os.path.isfile(self.executable):
            return self.executable
        return shutil.which(self.name)           # e.g. "cplex" on PATH

    @staticmethod
    def from_dict(d: dict) -> "SolverConfig":
        s = d["solver"]
        return SolverConfig(
            name=s["name"],
            executable=s.get("executable", ""),
            fallback_order=list(s.get("fallback_order", [s["name"]])),
            mip_gap=float(s["mip_gap"]),
            threads=int(s.get("threads", 0)),
            time_limit_sec={str(k): int(v) for k, v in s["time_limit_sec"].items()},
        )
