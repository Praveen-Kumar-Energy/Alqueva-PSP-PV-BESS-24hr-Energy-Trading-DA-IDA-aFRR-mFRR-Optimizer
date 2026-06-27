"""
ida1_reoptimiser.py — IDA1 binding to the shared intraday engine.

IDA1 re-optimises the whole day (all 24 hours tradable) against the DA committed
position using the IDA1 price curve. The physics live once in the shared engine.
"""
from __future__ import annotations

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model.ida_reoptimiser import reoptimise_ida


def optimise_ida1(delivery_date: str, cfg: AppConfig, no_pause: bool = False) -> dict:
    return reoptimise_ida("IDA1", delivery_date, cfg, no_pause=no_pause)
