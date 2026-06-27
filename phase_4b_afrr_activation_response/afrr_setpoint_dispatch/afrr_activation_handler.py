"""
afrr_activation_handler.py — aFRR activation response (FAT 5 min).

Binds the shared activation engine for aFRR: simulates the AGC calls during
delivery, confirms 5-min FAT deliverability, and logs activated energy.
"""
from __future__ import annotations

from common_layer.configuration.config_loader import AppConfig
from common_layer.optimisation_model.reserve_activation import (
    simulate_and_log_activation, ActivationSummary,
)


def handle_afrr_activation(delivery_date: str, cfg: AppConfig) -> ActivationSummary:
    return simulate_and_log_activation("aFRR", delivery_date, cfg,
                                       fat_min=cfg.market.afrr.fat_min)
