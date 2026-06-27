"""
timezone_utils.py — explicit market (CET) vs plant (Lisbon) time handling.

Spec INV-10: all gate times resolve in CET (Europe/Madrid); all plant/SCADA
times in Europe/Lisbon. Conversions are always explicit here — never implicit
elsewhere. Portugal is CET−1 in winter (WET) and CET in summer (WEST), so a
naive single-clock approach would mis-time gates twice a year.

MIBEL gate reference times (CET):
    DA   D-1 12:00  IDA1 D-1 15:00  IDA2 D-1 22:00  IDA3 D 10:00
    XBID continuous (H-1 rolling, not a fixed daily trigger)

Trigger spec grammar (from market.yaml):
    "D-1 12:00"  →  12:00 CET on the day before delivery day D
    "D 10:00"    →  10:00 CET on delivery day D
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("Europe/Madrid")    # CET/CEST — all MIBEL/OMIE gate deadlines
PLANT_TZ  = ZoneInfo("Europe/Lisbon")   # WET/WEST — plant operations and SCADA


def parse_trigger_spec(spec: str) -> tuple[int, int, int]:
    """Parse "D-1 12:00" / "D 10:00" -> (day_offset, hour, minute).

    day_offset is relative to delivery day D: 0 = D, -1 = day before."""
    day_part, time_part = spec.strip().split()
    if day_part == "D":
        offset = 0
    elif day_part.startswith("D-"):
        offset = -int(day_part[2:])
    elif day_part.startswith("D+"):
        offset = int(day_part[2:])
    else:
        raise ValueError(f"Unknown trigger day spec: {spec!r}")
    hh, mm = time_part.split(":")
    return offset, int(hh), int(mm)


def resolve_gate_time(spec: str, delivery_date: dt.date) -> dt.datetime:
    """Resolve a trigger/close spec to a timezone-aware CET datetime."""
    offset, hh, mm = parse_trigger_spec(spec)
    run_day = delivery_date + dt.timedelta(days=offset)
    return dt.datetime.combine(run_day, dt.time(hh, mm), tzinfo=MARKET_TZ)


def market_to_plant(ts: dt.datetime) -> dt.datetime:
    """Convert a CET market timestamp to plant (Lisbon) local time."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=MARKET_TZ)
    return ts.astimezone(PLANT_TZ)


def plant_to_market(ts: dt.datetime) -> dt.datetime:
    """Convert a plant (Lisbon) timestamp to CET market time."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=PLANT_TZ)
    return ts.astimezone(MARKET_TZ)


def now_market() -> dt.datetime:
    return dt.datetime.now(MARKET_TZ)


def now_plant() -> dt.datetime:
    return dt.datetime.now(PLANT_TZ)
