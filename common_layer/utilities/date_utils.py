"""
date_utils.py — delivery-date, hour and ISP helpers.

Centralises the calendar logic the markets depend on:
  * delivery hours 1..24 (DST days noted below),
  * ISP slots (96 x 15-min after the 19 Mar 2025 transition),
  * mapping between hour-of-day and ISP index,
  * SIDC 3-session regime detection (from 13 Jun 2024).

Note on DST: Continental DST days have 23 or 25 hours. The hourly products on
those two days per year carry 23/25 periods; this helper exposes `hours_in_day`
so callers never hard-code 24. ISP counts scale the same way (92 / 100).
"""
from __future__ import annotations

import datetime as dt
from typing import List

# Regime / transition dates (CONFIRMED — see market.yaml).
SIDC_3_SESSION_FROM = dt.date(2024, 6, 13)
PT_ISP_15MIN_FROM = dt.date(2025, 3, 19)


def parse_date(s: str) -> dt.date:
    """Parse 'YYYY-MM-DD' to a date."""
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def resolve_delivery_date(cli_date: str | None = None,
                          yaml_date: str | None = None) -> tuple[str, str, bool]:
    """Resolve the trading delivery date for a run.

    Priority: --date (CLI) > run.yaml `delivery_date`. The special value "auto"
    (or an empty value) selects AUTO mode: the delivery date becomes TOMORROW in
    Portugal local time (Europe/Lisbon), so a morning run needs no manual editing.
    Any explicit date (DD-MM-YYYY, or ISO YYYY-MM-DD) is MANUAL mode.

    Returns (iso, display, is_auto):
        iso     -> 'YYYY-MM-DD' (internal format passed to all phase functions)
        display -> 'DD-MM-YYYY' (for logs / UI)
        is_auto -> True when the date was auto-derived as tomorrow (Portugal)
    """
    raw = (cli_date or yaml_date or "auto").strip()
    if raw == "" or raw.lower() == "auto":
        from zoneinfo import ZoneInfo
        today_pt = dt.datetime.now(ZoneInfo("Europe/Lisbon")).date()  # Portugal today
        d = today_pt + dt.timedelta(days=1)                           # delivery = tomorrow
        return d.strftime("%Y-%m-%d"), d.strftime("%d-%m-%Y"), True
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(raw, fmt).date()
            return d.strftime("%Y-%m-%d"), d.strftime("%d-%m-%Y"), False
        except ValueError:
            continue
    raise ValueError(
        f"Unrecognised delivery date {raw!r}. Use DD-MM-YYYY (e.g. 06-07-2026) "
        f"or 'auto' for tomorrow in Portugal.")


def portugal_today() -> dt.date:
    """Current calendar date in Portugal (Europe/Lisbon)."""
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo("Europe/Lisbon")).date()


def hours_in_day(day: dt.date, tz_name: str = "Europe/Lisbon") -> int:
    """Number of clock hours in `day` accounting for DST (23/24/25)."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(tz_name)
    start = dt.datetime.combine(day, dt.time(0, 0), tzinfo=tz)
    nxt = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0, 0), tzinfo=tz)
    return round((nxt - start).total_seconds() / 3600.0)


def delivery_hours(day: dt.date) -> List[int]:
    """List of delivery hours [1..N] for the day (N = 23/24/25)."""
    return list(range(1, hours_in_day(day) + 1))


def isp_duration_min(day: dt.date) -> int:
    """ISP duration in minutes: 60 pre-transition (hourly), 15 after 19 Mar 2025."""
    return 15 if day >= PT_ISP_15MIN_FROM else 60


def isp_per_day(day: dt.date) -> int:
    """ISP count for the day: 96 post-transition (4 × 24), scaled for DST (92/100 on change days)."""
    if day < PT_ISP_15MIN_FROM:
        return hours_in_day(day)          # 1 ISP/hour pre-transition
    return hours_in_day(day) * 4


def hour_to_isps(hour: int, day: dt.date) -> List[int]:
    """ISP indices (1-based) belonging to delivery `hour` on `day`."""
    if day < PT_ISP_15MIN_FROM:
        return [hour]
    first = (hour - 1) * 4 + 1
    return [first, first + 1, first + 2, first + 3]


def isp_to_hour(isp: int, day: dt.date) -> int:
    """Delivery hour (1-based) that contains ISP index `isp`."""
    if day < PT_ISP_15MIN_FROM:
        return isp
    return (isp - 1) // 4 + 1


def is_three_session_regime(day: dt.date) -> bool:
    """True if `day` is in the SIDC 3-session (IDA1/2/3) intraday regime."""
    return day >= SIDC_3_SESSION_FROM
