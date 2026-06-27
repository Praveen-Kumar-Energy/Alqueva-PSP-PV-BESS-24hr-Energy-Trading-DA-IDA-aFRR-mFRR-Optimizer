"""
ida3_reoptimiser.py — IDA3 binding to the shared intraday engine.

IDA3 re-optimises ONLY delivery hours 12-24 against the IDA2 committed position;
hours 1-11 are frozen to their committed net (spec INV-11). The shared engine
reads the tradable window from the IDA3 gate config and freezes the rest.
"""
from __future__ import annotations

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model.ida_reoptimiser import reoptimise_ida


def optimise_ida3(delivery_date: str, cfg: AppConfig, no_pause: bool = False) -> dict:
    return reoptimise_ida("IDA3", delivery_date, cfg, no_pause=no_pause)
