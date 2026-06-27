"""
optimisation_model — the shared 24h portfolio MILP.

One model for DA and every IDA gate; build it with the right prices/forecasts
and (optionally) freeze hours an IDA cannot re-trade.
"""
from common_layer.optimisation_model.core_milp_builder import (
    build_core_model, CoreModelMeta,
)
from common_layer.optimisation_model.core_milp_solver import (
    solve_core_model, extract_results, GateResults, SolveError,
    analyze_binding_constraints,
)

__all__ = [
    "build_core_model", "CoreModelMeta",
    "solve_core_model", "extract_results", "GateResults", "SolveError",
    "analyze_binding_constraints",
]
