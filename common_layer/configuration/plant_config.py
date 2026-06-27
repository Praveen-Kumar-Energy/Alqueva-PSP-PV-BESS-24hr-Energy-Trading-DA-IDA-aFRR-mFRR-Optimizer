"""
plant_config.py — typed Alqueva plant parameters.

Loads config/plant.yaml into frozen dataclasses so every phase consumes the
same validated numbers with IDE auto-complete and no stray dict keys.

SIGN CONVENTION (enforced everywhere downstream):
    generation / discharge = POSITIVE (+)
    pumping / charging      = NEGATIVE (-)

All values trace to config/plant.yaml, which tags each as CONFIRMED (public
source) or ESTIMATED. Derived plant-level totals are computed here once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


# ---------------------------------------------------------------------------
# Pumped-storage units
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PSPConfig:
    n_units: int                           # 4 reversible Francis units
    p_turbine_nameplate_mw: float          # 129.6 MW per unit (CONFIRMED)
    p_turbine_max_mw: float                # net per unit after aux loads
    p_turbine_min_mw: float                # minimum stable load per unit (≈57 MW)
    p_pump_max_mw: float                   # per unit (ESTIMATED; 111.6 MW)
    q_turbine_max_m3h: float               # m3/h at full turbine power, per unit
    q_turbine_min_m3h: float               # m3/h at minimum stable turbine load, per unit
    q_pump_max_m3h: float                  # m3/h at full pump power, per unit
    q_pump_min_m3h: float                  # m3/h at minimum pump power, per unit
    startup_cost_eur: float                # cost penalty per cold start in objective
    ramp_rate_mw_per_min_per_unit: float   # MW/min per unit; plant total = ×4

    # --- derived plant-level totals (4 units) -----------------------------
    @property
    def total_turbine_max_mw(self) -> float:
        return self.n_units * self.p_turbine_max_mw      # 518.4 MW generation

    @property
    def total_pump_max_mw(self) -> float:
        return self.n_units * self.p_pump_max_mw         # 446.4 MW pump magnitude

    @property
    def total_ramp_mw_per_min(self) -> float:
        return self.n_units * self.ramp_rate_mw_per_min_per_unit

    @staticmethod
    def from_dict(d: dict) -> "PSPConfig":
        return PSPConfig(
            n_units=int(d["n_units"]),
            p_turbine_nameplate_mw=float(d["p_turbine_nameplate_mw"]),
            p_turbine_max_mw=float(d["p_turbine_max_mw"]),
            p_turbine_min_mw=float(d["p_turbine_min_mw"]),
            p_pump_max_mw=float(d["p_pump_max_mw"]),
            q_turbine_max_m3h=float(d["q_turbine_max_m3h"]),
            q_turbine_min_m3h=float(d["q_turbine_min_m3h"]),
            q_pump_max_m3h=float(d["q_pump_max_m3h"]),
            q_pump_min_m3h=float(d["q_pump_min_m3h"]),
            startup_cost_eur=float(d["startup_cost_eur"]),
            ramp_rate_mw_per_min_per_unit=float(d.get("ramp_rate_mw_per_min_per_unit", 25.0)),
        )


# ---------------------------------------------------------------------------
# Floating PV
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PVConfig:
    peak_capacity_mw: float           # 5 MWp nameplate (floating array)
    latitude: float                   # Alqueva reservoir (≈38.2 N)
    longitude: float                  # Alqueva reservoir (≈7.5 W)
    temperature_coeff_per_c: float    # power temperature coefficient γ (negative, e.g. -0.0040)
    t_ref_c: float                    # reference cell temperature for derate (25 °C)
    g_ref_wm2: float                  # reference irradiance (1000 W/m2)
    commission_year: int              # 2022 — used to compute degradation years
    degradation_rate_per_year: float  # annual output loss fraction (e.g. 0.005 = 0.5%/yr)

    @staticmethod
    def from_dict(d: dict) -> "PVConfig":
        return PVConfig(
            peak_capacity_mw=float(d["peak_capacity_mw"]),
            latitude=float(d["latitude"]),
            longitude=float(d["longitude"]),
            temperature_coeff_per_c=float(d["temperature_coeff_per_c"]),
            t_ref_c=float(d["t_ref_c"]),
            g_ref_wm2=float(d["g_ref_wm2"]),
            commission_year=int(d["commission_year"]),
            degradation_rate_per_year=float(d["degradation_rate_per_year"]),
        )


# ---------------------------------------------------------------------------
# Battery energy storage
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BESSConfig:
    power_mw: float                   # 1 MW rated charge/discharge power
    capacity_mwh: float               # 2 MWh nameplate energy capacity
    soc_min_frac: float               # 0.10 → E_min = 0.20 MWh (10 % floor)
    soc_max_frac: float               # 0.95 → E_max = 1.90 MWh (95 % ceiling)
    eta_charge: float                 # round-trip charge efficiency (< 1)
    eta_discharge: float              # round-trip discharge efficiency (< 1)
    initial_soc_frac: float           # state of charge at start of planning horizon
    afrr_fat_min: float               # aFRR full activation time used for deliverability (5 min)
    degradation_cost_eur_mwh: float   # cycle-degradation penalty added to objective

    @property
    def e_min_mwh(self) -> float:
        return self.soc_min_frac * self.capacity_mwh     # 0.20 MWh

    @property
    def e_max_mwh(self) -> float:
        return self.soc_max_frac * self.capacity_mwh     # 1.90 MWh

    @staticmethod
    def from_dict(d: dict) -> "BESSConfig":
        return BESSConfig(
            power_mw=float(d["power_mw"]),
            capacity_mwh=float(d["capacity_mwh"]),
            soc_min_frac=float(d["soc_min_frac"]),
            soc_max_frac=float(d["soc_max_frac"]),
            eta_charge=float(d["eta_charge"]),
            eta_discharge=float(d["eta_discharge"]),
            initial_soc_frac=float(d["initial_soc_frac"]),
            afrr_fat_min=float(d["afrr_fat_min"]),
            degradation_cost_eur_mwh=float(d["degradation_cost_eur_mwh"]),
        )


# ---------------------------------------------------------------------------
# Two-reservoir closed loop (Alqueva upper / Pedrógão lower)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReservoirConfig:
    """Head model: H_net = 54.7 + 7.89e-9 × (V_up_m³ − 830e6) m  →  range 54.7–73.0 m.
    Water balance per step: ΔV_upper = (inflow + q_pump − q_turb − spill) × dt / 1,000,000 hm³."""
    upper_capacity_hm3: float         # Alqueva gross storage capacity (hm3)
    upper_usable_hm3: float           # usable upper bound for operations (hm3)
    upper_min_hm3: float              # Alqueva operational minimum (hm3)
    upper_initial_hm3: float          # initial upper reservoir volume for planning horizon
    lower_capacity_hm3: float         # Pedrógão gross capacity (hm3)
    lower_initial_hm3: float          # initial Pedrógão volume for planning horizon
    lower_min_hm3: float              # Pedrógão operational minimum (hm3)
    monthly_inflow_m3h: Dict[int, float]  # natural inflow to upper reservoir by month (m3/h)

    def inflow_for_month(self, month: int) -> float:
        """Monthly mean natural inflow (m3/h); falls back to 340 m3/h if month missing."""
        return self.monthly_inflow_m3h.get(month, 340.0)

    @staticmethod
    def from_dict(d: dict) -> "ReservoirConfig":
        return ReservoirConfig(
            upper_capacity_hm3=float(d["upper_capacity_hm3"]),
            upper_usable_hm3=float(d["upper_usable_hm3"]),
            upper_min_hm3=float(d["upper_min_hm3"]),
            upper_initial_hm3=float(d["upper_initial_hm3"]),
            lower_capacity_hm3=float(d["lower_capacity_hm3"]),
            lower_initial_hm3=float(d["lower_initial_hm3"]),
            lower_min_hm3=float(d["lower_min_hm3"]),
            monthly_inflow_m3h={int(k): float(v) for k, v in d["monthly_inflow_m3h"].items()},
        )


@dataclass(frozen=True)
class FCRConfig:
    """FCR (primary frequency control) is a mandatory non-remunerated grid-code obligation
    in PT/ES. It is reserved headroom only — never sold. Currently 0.0 MW (see plant.yaml)."""
    mandatory_headroom_mw: float      # MW kept free on both sides; 0.0 for Alqueva

    @staticmethod
    def from_dict(d: dict) -> "FCRConfig":
        return FCRConfig(mandatory_headroom_mw=float(d.get("mandatory_headroom_mw", 0.0)))


@dataclass(frozen=True)
class EconomicsConfig:
    pv_curtailment_penalty_eur_mwh: float  # penalty for curtailing available PV (€/MWh)
    spillage_penalty_eur_m3: float          # penalty per m3 spilled over dam (€/m3)
    water_value_eur_mwh: float             # opportunity cost of stored water (€/MWh equivalent)

    @staticmethod
    def from_dict(d: dict) -> "EconomicsConfig":
        return EconomicsConfig(
            pv_curtailment_penalty_eur_mwh=float(d["pv_curtailment_penalty_eur_mwh"]),
            spillage_penalty_eur_m3=float(d["spillage_penalty_eur_m3"]),
            water_value_eur_mwh=float(d.get("water_value_eur_mwh", 38.0)),
        )


@dataclass(frozen=True)
class InitialState:
    upper_reservoir_hm3: float
    lower_reservoir_hm3: float
    bess_soc_frac: float
    units_on: List[bool]

    @staticmethod
    def from_dict(d: dict) -> "InitialState":
        return InitialState(
            upper_reservoir_hm3=float(d["upper_reservoir_hm3"]),
            lower_reservoir_hm3=float(d["lower_reservoir_hm3"]),
            bess_soc_frac=float(d["bess_soc_frac"]),
            units_on=list(d["units_on"]),
        )


# ---------------------------------------------------------------------------
# Top-level plant config
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlantConfig:
    name: str
    country: str
    market_timezone: str
    plant_timezone: str
    psp: PSPConfig
    pv: PVConfig
    bess: BESSConfig
    reservoir: ReservoirConfig
    fcr: FCRConfig
    economics: EconomicsConfig
    initial_state: InitialState

    # --- plant-level power envelope (used by bid_checker / reserve sizing) --
    @property
    def p_max_generation_mw(self) -> float:
        """Max net generation: all turbines + PV + BESS discharge."""
        return (self.psp.total_turbine_max_mw
                + self.pv.peak_capacity_mw
                + self.bess.power_mw)

    @property
    def p_max_pump_mw(self) -> float:
        """Max net demand magnitude: all pumps + BESS charge (positive number)."""
        return self.psp.total_pump_max_mw + self.bess.power_mw

    @staticmethod
    def from_dict(d: dict) -> "PlantConfig":
        plant = d["plant"]
        return PlantConfig(
            name=plant["name"],
            country=plant["country"],
            market_timezone=plant["market_timezone"],
            plant_timezone=plant["plant_timezone"],
            psp=PSPConfig.from_dict(d["psp"]),
            pv=PVConfig.from_dict(d["pv"]),
            bess=BESSConfig.from_dict(d["bess"]),
            reservoir=ReservoirConfig.from_dict(d["reservoir"]),
            fcr=FCRConfig.from_dict(d.get("fcr", {})),
            economics=EconomicsConfig.from_dict(d["economics"]),
            initial_state=InitialState.from_dict(d["initial_state"]),
        )
