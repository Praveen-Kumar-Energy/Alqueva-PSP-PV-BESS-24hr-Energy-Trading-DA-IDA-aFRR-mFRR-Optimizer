"""
config_loader.py — single entry point that loads all configuration.

Reads config/plant.yaml, config/market.yaml, config/solver.yaml and builds one
typed AppConfig. Every module calls load_config() and never touches raw YAML.

The config/ directory is located relative to the repository root, which is the
parent of common_layer/. An explicit path can be passed for tests.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import yaml

from common_layer.configuration.plant_config import PlantConfig
from common_layer.configuration.market_config import MarketConfig
from common_layer.configuration.solver_config import SolverConfig


@dataclass(frozen=True)
class AppConfig:
    plant: PlantConfig
    market: MarketConfig
    solver: SolverConfig


def _repo_root() -> str:
    """Repository root = two levels up from this file (common_layer/configuration/)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir))


def _read_yaml(path: str) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} did not parse to a mapping.")
    return data


@lru_cache(maxsize=4)
def load_config(config_dir: Optional[str] = None) -> AppConfig:
    """Load and cache the full application configuration.

    Args:
        config_dir: path to the config/ folder. Defaults to <repo_root>/config.

    Returns:
        AppConfig(plant, market, solver) — fully typed, frozen.
    """
    cfg_dir = config_dir or os.path.join(_repo_root(), "config")

    plant_d  = _read_yaml(os.path.join(cfg_dir, "plant.yaml"))
    market_d = _read_yaml(os.path.join(cfg_dir, "market.yaml"))
    solver_d = _read_yaml(os.path.join(cfg_dir, "solver.yaml"))

    return AppConfig(
        plant=PlantConfig.from_dict(plant_d),
        market=MarketConfig.from_dict(market_d),
        solver=SolverConfig.from_dict(solver_d),
    )
