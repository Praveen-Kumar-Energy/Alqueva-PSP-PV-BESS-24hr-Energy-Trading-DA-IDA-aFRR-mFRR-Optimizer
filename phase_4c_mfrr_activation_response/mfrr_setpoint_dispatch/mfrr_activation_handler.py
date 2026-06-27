"""
mfrr_activation_handler.py — mFRR activation response (FAT 12.5 min).

Binds the shared activation engine for mFRR: simulates the TSO instructions
during delivery, confirms 12.5-min FAT deliverability, and logs activated energy.
"""
from __future__ import annotations

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model.reserve_activation import (
    simulate_and_log_activation, ActivationSummary,
)


def handle_mfrr_activation(delivery_date: str, cfg: AppConfig) -> ActivationSummary:
    return simulate_and_log_activation("mFRR", delivery_date, cfg,
                                       fat_min=cfg.market.mfrr.fat_min)
