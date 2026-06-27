"""
ida2_reoptimiser.py — IDA2 binding to the shared intraday engine.

IDA2 re-optimises the whole day against the IDA1 committed position under the
IDA2 price curve.
"""
from __future__ import annotations

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model.ida_reoptimiser import reoptimise_ida


def optimise_ida2(delivery_date: str, cfg: AppConfig, no_pause: bool = False) -> dict:
    return reoptimise_ida("IDA2", delivery_date, cfg, no_pause=no_pause)
