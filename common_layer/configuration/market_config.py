"""
market_config.py — typed MIBEL market parameters.

Loads config/market.yaml into frozen dataclasses: gate times, frequency bands,
bid limits, reserve and imbalance settings, trading thresholds.

All gate times are CET strings ("D-1 12:00"); resolution to wall-clock happens
in the gate scheduler, never here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Energy market gates
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GateConfig:
    name: str
    description: str
    delivery_hours: List[int]        # [first, last] inclusive
    pipeline_trigger: Optional[str] = None
    gate_close: Optional[str] = None
    resolution_h: int = 1
    gate_type: str = "auction"       # "auction" or "continuous"
    gate_closure_hours_before_delivery: Optional[int] = None
    check_window_1_trigger: Optional[str] = None
    check_window_2_trigger: Optional[str] = None

    def hour_in_product(self, hour: int) -> bool:
        """True if delivery `hour` (1..24) belongs to this gate's product.

        IDA3 (D 10:00 CET) covers hours 12-24 only — hours 1-11 settled in DA/IDA1/2
        are final and must never be re-bid (spec INV-11)."""
        lo, hi = self.delivery_hours
        return lo <= hour <= hi

    @staticmethod
    def from_dict(name: str, d: dict) -> "GateConfig":
        return GateConfig(
            name=name,
            description=d.get("description", ""),
            delivery_hours=list(d["delivery_hours"]),
            pipeline_trigger=d.get("pipeline_trigger"),
            gate_close=d.get("gate_close"),
            resolution_h=int(d.get("resolution_h", 1)),
            gate_type=d.get("type", "auction"),
            gate_closure_hours_before_delivery=d.get("gate_closure_hours_before_delivery"),
            check_window_1_trigger=d.get("check_window_1_trigger"),
            check_window_2_trigger=d.get("check_window_2_trigger"),
        )


# ---------------------------------------------------------------------------
# Frequency bands (Continental Europe synchronous area)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReserveBand:
    """One CE synchronous-area reserve product's frequency response band."""
    typical_band_hz: float
    full_activation_time: float      # seconds for FCR; minutes for aFRR/mFRR
    time_unit: str                   # "s" or "min"


@dataclass(frozen=True)
class FrequencyConfig:
    nominal_hz: float
    fcr_deadband_mhz: float
    fcr_full_activation_hz: float
    fcr_full_activation_time_s: float
    afrr_band_hz: float
    afrr_fat_min: float
    mfrr_band_hz: float
    mfrr_fat_min: float

    @staticmethod
    def from_dict(d: dict) -> "FrequencyConfig":
        fcr = d["fcr"]; afrr = d["afrr"]; mfrr = d["mfrr"]
        return FrequencyConfig(
            nominal_hz=float(d["nominal_hz"]),
            fcr_deadband_mhz=float(fcr["deadband_mhz"]),
            fcr_full_activation_hz=float(fcr["full_activation_hz"]),
            fcr_full_activation_time_s=float(fcr["full_activation_time_s"]),
            afrr_band_hz=float(afrr["typical_band_hz"]),
            afrr_fat_min=float(afrr["full_activation_time_min"]),
            mfrr_band_hz=float(mfrr["typical_band_hz"]),
            mfrr_fat_min=float(mfrr["full_activation_time_min"]),
        )


# ---------------------------------------------------------------------------
# Bid limits, reserves, imbalance, thresholds
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BidLimits:
    price_min_eur_mwh: float
    price_max_eur_mwh: float
    max_generation_mw: float
    max_pump_mw: float

    @staticmethod
    def from_dict(d: dict) -> "BidLimits":
        return BidLimits(
            price_min_eur_mwh=float(d["price_min_eur_mwh"]),
            price_max_eur_mwh=float(d["price_max_eur_mwh"]),
            max_generation_mw=float(d["max_generation_mw"]),
            max_pump_mw=float(d["max_pump_mw"]),
        )


@dataclass(frozen=True)
class AFRRConfig:
    """PICASSO platform aFRR parameters. FAT = 5 min → eff_isp_h = (15-2.5)/60 = 0.208333 h."""
    platform: str
    gate_close: str
    max_offer_up_mw: float
    max_offer_dn_mw: float
    cap_price_max_eur_mw: float
    fat_min: float                    # full activation time (min); 5 min for PICASSO aFRR

    @staticmethod
    def from_dict(d: dict, fat_min: float) -> "AFRRConfig":
        return AFRRConfig(
            platform=d["platform"],
            gate_close=d["gate_close"],
            max_offer_up_mw=float(d["max_offer_up_mw"]),
            max_offer_dn_mw=float(d["max_offer_dn_mw"]),
            cap_price_max_eur_mw=float(d["cap_price_max_eur_mw"]),
            fat_min=fat_min,
        )


@dataclass(frozen=True)
class MFRRConfig:
    """MARI platform mFRR parameters. FAT = 12.5 min → eff_isp_h = (15-6.25)/60 = 0.145833 h."""
    mari_live_date: str
    gate_close: str
    max_offer_fraction: float         # fraction of remaining headroom after aFRR
    price_fraction_of_afrr: float
    fat_min: float                    # full activation time (min); 12.5 min for MARI mFRR

    @staticmethod
    def from_dict(d: dict, fat_min: float) -> "MFRRConfig":
        return MFRRConfig(
            mari_live_date=d["mari_live_date"],
            gate_close=d["gate_close"],
            max_offer_fraction=float(d["max_offer_fraction"]),
            price_fraction_of_afrr=float(d["price_fraction_of_afrr"]),
            fat_min=fat_min,
        )


@dataclass(frozen=True)
class ImbalanceConfig:
    """MIBEL imbalance settlement.  Fallback when live prices unavailable:
    long (oversupply) → DA × fallback_long_factor (0.85),
    short (deficit)   → DA × fallback_short_factor (1.20), ISP = 0.25 h."""
    pricing_type: str
    source: str
    resolution: str                   # e.g. "15min" post PT-ISP transition
    fallback_short_factor: float      # short-position penalty multiplier  (default 1.20)
    fallback_long_factor: float       # long-position discount multiplier  (default 0.85)

    @staticmethod
    def from_dict(d: dict) -> "ImbalanceConfig":
        return ImbalanceConfig(
            pricing_type=d["pricing_type"],
            source=d["source"],
            resolution=d["resolution"],
            fallback_short_factor=float(d["fallback_short_factor"]),
            fallback_long_factor=float(d["fallback_long_factor"]),
        )


@dataclass(frozen=True)
class TradingThresholds:
    """Minimum economic conditions required before placing an IDA or XBID order.
    Guards against trading noise that costs more in imbalance risk than the spread earns."""
    ida_min_delta_mwh: float          # volume change required to justify a re-bid
    ida_min_spread_eur_mwh: float     # minimum IDA vs DA price spread to trade
    ida_min_rebid_eur_floor: float    # absolute EUR floor — never re-bid below this
    ida_min_rebid_pct: float          # dynamic: % of DA position value in tradable hours
    xbid_min_spread_eur_mwh: float    # minimum XBID vs committed spread to execute
    xbid_max_slippage_eur: float      # maximum allowed slippage per XBID order
    xbid_max_volume_per_order_mw: float  # XBID order size cap (avoids market impact)

    @staticmethod
    def from_dict(d: dict) -> "TradingThresholds":
        return TradingThresholds(
            ida_min_delta_mwh=float(d["ida_min_delta_mwh"]),
            ida_min_spread_eur_mwh=float(d["ida_min_spread_eur_mwh"]),
            ida_min_rebid_eur_floor=float(d.get("ida_min_rebid_eur_floor", 30.0)),
            ida_min_rebid_pct=float(d.get("ida_min_rebid_pct", 0.15)),
            xbid_min_spread_eur_mwh=float(d["xbid_min_spread_eur_mwh"]),
            xbid_max_slippage_eur=float(d["xbid_max_slippage_eur"]),
            xbid_max_volume_per_order_mw=float(d["xbid_max_volume_per_order_mw"]),
        )


@dataclass(frozen=True)
class BalancingConfig:
    """Portuguese balancing market settings post-19 Mar 2025 ISP transition (96 × 15-min ISPs)."""
    isp_per_hour: int                 # 4 after the 15-min transition (1 before)
    isp_duration_min: int             # 15 after transition, 60 before
    portugal_isp_transition_date: str # "2025-03-19" — Portugal adopts 15-min ISP
    afrr_fat_min: float               # PICASSO aFRR full activation time: 5 min
    mfrr_fat_min: float               # MARI mFRR full activation time: 12.5 min

    @staticmethod
    def from_dict(d: dict) -> "BalancingConfig":
        return BalancingConfig(
            isp_per_hour=int(d["isp_per_hour"]),
            isp_duration_min=int(d["isp_duration_min"]),
            portugal_isp_transition_date=d["portugal_isp_transition_date"],
            afrr_fat_min=float(d["afrr_fat_min"]),
            mfrr_fat_min=float(d["mfrr_fat_min"]),
        )


# ---------------------------------------------------------------------------
# Top-level market config
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MarketConfig:
    zone: str
    energy_operator: str
    balancing_operator: str
    currency: str
    gates: Dict[str, GateConfig]
    sessions_3_from_date: str
    balancing: BalancingConfig
    frequency: FrequencyConfig
    bid_limits: BidLimits
    afrr: AFRRConfig
    mfrr: MFRRConfig
    imbalance: ImbalanceConfig
    trading_thresholds: TradingThresholds

    def gate(self, name: str) -> GateConfig:
        if name not in self.gates:
            raise KeyError(f"Unknown gate {name!r}. Known: {list(self.gates)}")
        return self.gates[name]

    @staticmethod
    def from_dict(d: dict) -> "MarketConfig":
        m = d["market"]
        gates = {name: GateConfig.from_dict(name, gd) for name, gd in d["gates"].items()}
        balancing = BalancingConfig.from_dict(d["balancing"])
        return MarketConfig(
            zone=m["zone"],
            energy_operator=m["energy_operator"],
            balancing_operator=m["balancing_operator"],
            currency=m["currency"],
            gates=gates,
            sessions_3_from_date=d["ida_regime"]["sessions_3_from_date"],
            balancing=balancing,
            frequency=FrequencyConfig.from_dict(d["frequency"]),
            bid_limits=BidLimits.from_dict(d["bid_limits"]),
            afrr=AFRRConfig.from_dict(d["afrr"], balancing.afrr_fat_min),
            mfrr=MFRRConfig.from_dict(d["mfrr"], balancing.mfrr_fat_min),
            imbalance=ImbalanceConfig.from_dict(d["imbalance"]),
            trading_thresholds=TradingThresholds.from_dict(d["trading_thresholds"]),
        )
