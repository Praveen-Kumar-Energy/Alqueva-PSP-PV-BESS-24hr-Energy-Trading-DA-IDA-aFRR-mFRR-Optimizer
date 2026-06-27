"""
afrr_activation_logger.py — read back the logged aFRR activations.

The activation engine writes to ActivationStore during delivery; this reader
surfaces the per-ISP activated energy for display and for Phase 5B settlement.
"""
from __future__ import annotations

from typing import List

from common_layer.database import ActivationStore


def load_afrr_activations(delivery_date: str) -> List[dict]:
    return ActivationStore().load(delivery_date, "aFRR")
