"""
run_production.py
=================
Alqueva PSP-PV-BESS  24-Hour Energy Trading Pipeline Orchestrator.

Runs all 14 pipeline phases in delivery order for a single date.
Reads config/run.yaml for settings; every option is overridable via CLI.

QUICK START
-----------
Just run (AUTO mode is the default — delivery date = tomorrow in Portugal):

    python run_production.py

COMMON CLI OVERRIDES  (no YAML edit needed)
-------------------------------------------
    # Run for a specific date
    python run_production.py --date 08-07-2026

    # Recovery: skip phases already completed, restart from real-time dispatch
    python run_production.py --date 08-07-2026 --from-phase realtime

    # Backtest: fully automated, synthetic prices, no live API calls
    python run_production.py --date 08-07-2026 --auto --synthetic

    # Run only selected phases (good for debugging individual phases)
    python run_production.py --date 08-07-2026 --only da,afrr,mfrr

    # Validate config and imports without executing anything
    python run_production.py --dry-run

PHASE KEYS (--from-phase / --only)
------------------------------------
  da  ida1  ida2  ida3  xbid
  afrr  mfrr  realtime
  afrr_activation  mfrr_activation
  energy_settlement  reserve_settlement  imbalance_settlement  analytics

EXIT CODES
----------
  0   All enabled phases passed or warned (non-critical).
  1   One or more critical phases failed.
  2   Configuration or import error.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import logging
import os
import sys
import time
from typing import Dict, List, Optional, Set

import yaml

# ---------------------------------------------------------------------------
# Phase output silencer.
# Each phase prints verbose tables and log lines.  We redirect all of that to
# a per-run log file so only the orchestrator status table appears on screen.
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _silence(log_fh: io.TextIOWrapper):
    """Redirect stdout + every active logger to an already-open log file.

    The file is opened ONCE by the caller and kept open for the entire
    pipeline run, so handlers never point to a closed stream.
    """
    old_stdout = sys.stdout
    sys.stdout = log_fh

    # Collect ALL loggers: root + every named logger registered so far.
    all_loggers = [logging.getLogger()] + [
        logging.getLogger(n) for n in logging.Logger.manager.loggerDict
    ]

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")
    file_handler = logging.StreamHandler(log_fh)
    file_handler.setFormatter(fmt)

    # Save and replace each logger's handlers.
    saved: dict = {}
    for lg in all_loggers:
        saved[id(lg)] = (lg, lg.handlers[:], lg.level, lg.propagate)
        if lg.name == "root":
            lg.handlers = [file_handler]
            lg.setLevel(logging.DEBUG)
        else:
            lg.handlers = []      # named loggers propagate to root
            lg.propagate = True

    try:
        yield
    finally:
        sys.stdout = old_stdout
        for _, (lg, handlers, level, prop) in saved.items():
            lg.handlers  = handlers
            lg.level     = level
            lg.propagate = prop


# ---------------------------------------------------------------------------
# Date parsing — user-facing format is DD-MM-YYYY.
# Internal format passed to all phase functions is YYYY-MM-DD (ISO 8601).
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> str:
    """Accept DD-MM-YYYY (user input / YAML) and return YYYY-MM-DD.

    Also accepts YYYY-MM-DD transparently so old scripts still work.
    Raises ValueError with a clear message on any other format.
    """
    raw = raw.strip()
    from datetime import datetime
    for fmt_in, fmt_out in (
        ("%d-%m-%Y", "%Y-%m-%d"),   # DD-MM-YYYY  (primary)
        ("%Y-%m-%d", "%Y-%m-%d"),   # YYYY-MM-DD  (fallback / ISO)
    ):
        try:
            return datetime.strptime(raw, fmt_in).strftime(fmt_out)
        except ValueError:
            continue
    raise ValueError(
        f"Unrecognised date '{raw}'. Use DD-MM-YYYY (e.g. 06-07-2026)."
    )


# ---------------------------------------------------------------------------
# Ensure repo root is importable.
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---------------------------------------------------------------------------
# Status tokens
# ---------------------------------------------------------------------------
PASS = "PASS"
SKIP = "SKIP"
WARN = "WARN"
FAIL = "FAIL"

# Status codes from individual run_*() functions.
_OK_CODES   = {"SUBMITTED", "OK", "NO_CHANGE"}
_WARN_CODES = {"NO_OFFER", "REJECTED"}

# ---------------------------------------------------------------------------
# Pipeline registry
#   (internal_key, display_label, is_critical)
#
# is_critical=True  → pipeline aborts immediately if this phase FAILs.
# is_critical=False → WARN logged, subsequent phases still run.
# ---------------------------------------------------------------------------
_PHASES: List[tuple] = [
    ("da",                  "1      Day-Ahead bidding  (OMIE DA)",        True),
    ("ida1",                "2A     IDA1 intraday re-optimisation",       True),
    ("ida2",                "2B     IDA2 intraday re-optimisation",       True),
    ("ida3",                "2C     IDA3 intraday re-optimisation",       True),
    ("xbid_w1",             "2D/W1  XBID continuous  (D-1 18:30)",       False),
    ("xbid_w2",             "2D/W2  XBID continuous  (D  09:30)",        False),
    ("afrr",                "3A     aFRR capacity offer  (PICASSO/REN)", True),
    ("mfrr",                "3B     mFRR capacity offer  (MARI)",        False),
    ("realtime",            "4A     RT dispatch simulation  (96 ISPs)",   True),
    ("afrr_activation",     "4B     aFRR activation response",            True),
    ("mfrr_activation",     "4C     mFRR activation response",            False),
    ("energy_settlement",   "5A     Energy settlement  (DA / IDA)",       True),
    ("reserve_settlement",  "5B     Reserve settlement  (aFRR / mFRR)",   True),
    ("imbalance_settlement","5C     Imbalance settlement  (REN balance)", True),
    ("analytics",           "5D     Analytics + KPI report + Excel",      False),
]

# The YAML phases block uses "xbid" to gate both W1 and W2.
_YAML_KEY = {"xbid_w1": "xbid", "xbid_w2": "xbid"}
_ALL_KEYS  = [k for k, _, _ in _PHASES]


# ---------------------------------------------------------------------------
# Phase dispatcher
# ---------------------------------------------------------------------------

def _dispatch(key: str, date: str, cfg, syn: bool, auto: bool) -> tuple[str, str]:
    """Call the phase runner; return (PASS|WARN|FAIL, one-line detail)."""

    def _run(fn, *a, **kw) -> tuple[str, str]:
        r = fn(*a, **kw)
        st = r.get("status", "UNKNOWN") if isinstance(r, dict) else str(r)
        if st in _OK_CODES:
            return PASS, _detail(r)
        if st in _WARN_CODES:
            return WARN, st
        if st == "SKIPPED":
            reason = r.get("reason", "skipped") if isinstance(r, dict) else "skipped"
            return SKIP, reason[:50]
        reason = ""
        if isinstance(r, dict):
            reason = r.get("reason", r.get("violations", ""))
        return FAIL, f"{st}: {reason}"

    if key == "da":
        from phase_1_da_day_ahead_bidding.run_da import run_da
        return _run(run_da, date, use_synthetic=syn, auto_approve=auto)

    if key == "ida1":
        from phase_2a_ida1_intraday_auction_1.ida1_milp_reoptimiser.ida1_reoptimiser import optimise_ida1
        return _run(optimise_ida1, date, cfg, no_pause=auto)

    if key == "ida2":
        from phase_2b_ida2_intraday_auction_2.ida2_milp_reoptimiser.ida2_reoptimiser import optimise_ida2
        return _run(optimise_ida2, date, cfg, no_pause=auto)

    if key == "ida3":
        from phase_2c_ida3_intraday_auction_3.ida3_milp_reoptimiser.ida3_reoptimiser import optimise_ida3
        return _run(optimise_ida3, date, cfg, no_pause=auto)

    if key in ("xbid_w1", "xbid_w2"):
        from phase_2d_xbid_continuous_intraday.xbid_milp_optimiser.xbid_optimiser import optimise_xbid
        return _run(optimise_xbid, date, cfg,
                    window="W1" if key == "xbid_w1" else "W2", no_pause=auto)

    if key == "afrr":
        from phase_3a_afrr_automatic_frequency_reserve.run_afrr import run_afrr
        return _run(run_afrr, date, cfg, no_pause=auto, use_synthetic=syn)

    if key == "mfrr":
        from phase_3b_mfrr_manual_frequency_reserve.run_mfrr import run_mfrr
        return _run(run_mfrr, date, cfg, no_pause=auto, use_synthetic=syn)

    if key == "realtime":
        from phase_4a_isp_real_time_dispatch.run_realtime import run_realtime
        return _run(run_realtime, date, cfg, no_pause=auto)

    if key == "afrr_activation":
        from phase_4b_afrr_activation_response.run_afrr_activation import run_afrr_activation
        return _run(run_afrr_activation, date, cfg, no_pause=auto)

    if key == "mfrr_activation":
        from phase_4c_mfrr_activation_response.run_mfrr_activation import run_mfrr_activation
        return _run(run_mfrr_activation, date, cfg, no_pause=auto)

    if key == "energy_settlement":
        from phase_5a_da_ida_settlement.run_energy_settlement import run_energy_settlement
        return _run(run_energy_settlement, date)

    if key == "reserve_settlement":
        from phase_5b_reserve_settlement.run_reserve_settlement import run_reserve_settlement
        return _run(run_reserve_settlement, date)

    if key == "imbalance_settlement":
        from phase_5c_imbalance_settlement.run_imbalance_settlement import run_imbalance_settlement
        return _run(run_imbalance_settlement, date)

    if key == "analytics":
        from phase_5d_analytics_and_reporting.run_analytics import run_analytics
        return _run(run_analytics, date, cfg, export_excel=True)

    return FAIL, f"Unknown phase key: {key}"


def _detail(r) -> str:
    """Extract a compact one-line financial note from a phase result dict."""
    if not isinstance(r, dict):
        return ""
    parts = []
    for k in ("energy_revenue_eur", "capacity_revenue_eur", "total_pnl_eur",
              "objective_eur", "pnl_change_eur", "activation_eur"):
        v = r.get(k)
        if v is not None:
            parts.append(f"{k.replace('_eur','').replace('_',' ')} {v:+,.0f}")
    return "  |  ".join(parts) if parts else (r.get("ref") or "")


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
_W = 82   # total line width

def _header(date: str, mode: str, source: str, dry: bool) -> None:
    print()
    print("=" * _W)
    print("  ALQUEVA PSP-PV-BESS   24-HOUR ENERGY TRADING PIPELINE")
    print("=" * _W)
    print(f"  Delivery date : {date}" + ("  [DRY-RUN — no submissions]" if dry else ""))
    print(f"  Mode          : {mode.upper()}")
    print(f"  Data source   : {source.upper()}")
    print("=" * _W)
    print(f"  {'PHASE':<44}  {'STATUS':^8}  {'TIME':>6}  NOTE")
    print("  " + "-" * (_W - 2))


def _row(label: str, status: str, elapsed: float, detail: str) -> None:
    icons = {PASS: "[ OK  ]", SKIP: "[  -- ]", WARN: "[ !!  ]", FAIL: "[ XX  ]"}
    t     = f"{elapsed:.2f}s" if elapsed > 0.001 else ""
    note  = detail[:34] if detail else ""
    print(f"  {label:<44}  {icons.get(status,'[????]')}  {t:>6}  {note}")


def _footer(results: list, total: float) -> int:
    n = {s: sum(1 for r in results if r["status"] == s)
         for s in (PASS, SKIP, WARN, FAIL)}
    print()
    print("=" * _W)
    outcome = "PIPELINE COMPLETE" if n[FAIL] == 0 else "PIPELINE FAILED"
    print(f"  {outcome}")
    print(f"  {n[PASS]} passed   {n[SKIP]} skipped   "
          f"{n[WARN]} warnings   {n[FAIL]} failed   "
          f"({total:.1f}s total)")
    fails = [r for r in results if r["status"] == FAIL]
    if fails:
        print()
        print("  FAILURES:")
        for r in fails:
            print(f"    [{r['key']}]  {r['detail']}")
    print("=" * _W)
    print()
    return 0 if n[FAIL] == 0 else 1


# ---------------------------------------------------------------------------
# YAML config loader
# ---------------------------------------------------------------------------

def _load_yaml(config_dir: Optional[str]) -> dict:
    path = os.path.join(
        config_dir or os.path.join(_ROOT, "config"), "run.yaml")
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"run.yaml not found: {path}\n"
            "Create config/run.yaml (copy from config/run.yaml.example) "
            "or pass --config <dir>."
        )
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"run.yaml did not parse to a mapping: {path}")
    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_production.py",
        description="Alqueva 24-hour energy trading pipeline orchestrator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--date",       metavar="DD-MM-YYYY",
                   help="Delivery date DD-MM-YYYY (e.g. 06-07-2026) for a MANUAL run, "
                        "or 'auto' for tomorrow in Portugal. Default (no flag) is AUTO.")
    p.add_argument("--config",     metavar="DIR",
                   help="Config directory (default: <repo>/config/)")
    p.add_argument("--from-phase", metavar="KEY", dest="from_phase",
                   help="Start pipeline from this phase (recovery restart)")
    p.add_argument("--only",       metavar="K1,K2,...",
                   help="Run only these phase keys (comma-separated)")
    p.add_argument("--auto",       action="store_true",
                   help="Auto mode: no operator prompts — overrides run.yaml")
    p.add_argument("--synthetic",  action="store_true",
                   help="Synthetic data: no live API calls — overrides run.yaml")
    p.add_argument("--dry-run",    dest="dry_run", action="store_true",
                   help="Validate config and imports; run nothing")
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ns = _build_parser().parse_args()

    # ── 1. Load YAML ────────────────────────────────────────────────────────
    try:
        yml = _load_yaml(ns.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n  ERROR: {exc}", file=sys.stderr)
        return 2

    # ── 2. Resolve delivery date (default AUTO = tomorrow in Portugal) ───────
    #   --date DD-MM-YYYY  -> manual (overrides yaml)
    #   run.yaml: auto     -> tomorrow in Portugal (Europe/Lisbon), no editing
    from common_layer.utilities.date_utils import resolve_delivery_date, portugal_today
    try:
        date, display_date, is_auto = resolve_delivery_date(
            ns.date, yml.get("delivery_date"))
    except ValueError as exc:
        print(f"\n  ERROR: {exc}", file=sys.stderr)
        return 2
    if is_auto:
        print(f"  Delivery date: AUTO -> {display_date}  "
              f"(tomorrow; today in Portugal is {portugal_today().strftime('%d-%m-%Y')})")
    else:
        print(f"  Delivery date: {display_date}  (manual)")
    mode   = "auto"      if ns.auto      else str(yml.get("mode",        "auto"))
    source = "synthetic" if ns.synthetic else str(yml.get("data_source", "synthetic"))
    is_auto = (mode   == "auto")
    is_syn  = (source == "synthetic")
    enabled: Dict[str, bool] = yml.get("phases", {})

    # ── 3. --from-phase index ───────────────────────────────────────────────
    from_idx = 0
    if ns.from_phase:
        fk = "xbid_w1" if ns.from_phase.strip() == "xbid" else ns.from_phase.strip()
        if fk not in _ALL_KEYS:
            print(f"\n  ERROR: Unknown phase key '{ns.from_phase}'. "
                  f"Valid: {', '.join(_ALL_KEYS)}", file=sys.stderr)
            return 2
        from_idx = _ALL_KEYS.index(fk)

    # ── 4. --only filter ────────────────────────────────────────────────────
    only_keys: Optional[Set[str]] = None
    if ns.only:
        raw = {k.strip() for k in ns.only.split(",")}
        only_keys = set()
        for k in raw:
            if k == "xbid":
                only_keys.update({"xbid_w1", "xbid_w2"})
            else:
                only_keys.add(k)
        bad = only_keys - set(_ALL_KEYS)
        if bad:
            print(f"\n  ERROR: Unknown phase key(s): {', '.join(sorted(bad))}",
                  file=sys.stderr)
            return 2

    # ── 5. Load plant/market/solver config ──────────────────────────────────
    try:
        from common_layer.configuration import load_config
        app_cfg = load_config(ns.config)
    except Exception as exc:
        print(f"\n  ERROR loading plant/market config: {exc}", file=sys.stderr)
        return 2

    # ── 6. Execute pipeline ─────────────────────────────────────────────────
    log_path = os.path.join(_ROOT, "runtime", "logs", f"pipeline_{date}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    _header(display_date, mode, source, ns.dry_run)
    if not ns.dry_run:
        print(f"  Log file      : runtime/logs/pipeline_{date}.log")
        print("  " + "-" * (_W - 2))

    critical_map = {k: c for k, _, c in _PHASES}
    results: List[dict] = []
    t_start = time.perf_counter()

    _log_fh = open(log_path, "a", encoding="utf-8") if not ns.dry_run else None

    for idx, (key, label, critical) in enumerate(_PHASES):
        yaml_key = _YAML_KEY.get(key, key)

        # Determine skip condition
        if only_keys is not None and key not in only_keys:
            r = dict(key=key, status=SKIP, detail="", elapsed=0.0)
            results.append(r)
            _row(label, SKIP, 0.0, "")
            continue
        if not enabled.get(yaml_key, True):
            r = dict(key=key, status=SKIP, detail="disabled in run.yaml", elapsed=0.0)
            results.append(r)
            _row(label, SKIP, 0.0, "disabled in run.yaml")
            continue
        if idx < from_idx:
            r = dict(key=key, status=SKIP, detail="--from-phase", elapsed=0.0)
            results.append(r)
            _row(label, SKIP, 0.0, "--from-phase")
            continue
        if ns.dry_run:
            r = dict(key=key, status=SKIP, detail="dry-run", elapsed=0.0)
            results.append(r)
            _row(label, SKIP, 0.0, "dry-run")
            continue

        # Run the phase.
        # Auto mode: silence all phase output to log file — clean table only.
        # Trader mode: let output flow so operator sees bid tables and can
        #              type A/R or press ENTER at each gate.
        t0 = time.perf_counter()
        try:
            if is_auto and _log_fh:
                with _silence(_log_fh):
                    status, detail = _dispatch(key, date, app_cfg, is_syn, is_auto)
            else:
                phase_label = f"[ PHASE {label.split()[0]} : {label.split(None,1)[1].strip()} ]"
                total   = 100 - 2         # fit Spyder console width (~100 chars)
                pad     = total - len(phase_label)
                left_n  = pad // 2
                right_n = pad - left_n
                left_stars  = ("* " * (left_n  // 2 + 1))[:left_n]
                right_stars = (" *" * (right_n // 2 + 1))[:right_n]
                bar = left_stars + phase_label + right_stars
                print()
                print()
                print()
                print("  " + bar)
                print()
                print()
                print()
                status, detail = _dispatch(key, date, app_cfg, is_syn, is_auto)
                print()
                print()
                print()
        except Exception as exc:
            status, detail = FAIL, str(exc)
        elapsed = time.perf_counter() - t0

        r = dict(key=key, status=status, detail=detail, elapsed=elapsed)
        results.append(r)
        _row(label, status, elapsed, detail)

        # Critical failure → abort remaining phases
        if status == FAIL and critical_map[key]:
            print(f"\n  ABORT: critical failure in phase [{key}]\n"
                  f"         {detail}")
            for rk, rl, _ in _PHASES[idx + 1:]:
                a = dict(key=rk, status=SKIP, detail="aborted", elapsed=0.0)
                results.append(a)
                _row(rl, SKIP, 0.0, "aborted")
            break

    if _log_fh:
        _log_fh.close()

    rc = _footer(results, time.perf_counter() - t_start)
    if not ns.dry_run:
        print(f"  Full phase output: runtime/logs/pipeline_{date}.log\n")

    # Auto-generate figures after every successful (or partial) run.
    # Figures overwrite the previous set — figures/output/ always reflects
    # the most recent run. Skipped on --dry-run and on critical abort.
    if not ns.dry_run and rc in (0, 1):
        _generate_figures(date)

    return rc


def _generate_figures(date: str) -> None:
    """Run the figure package; warn but never crash the pipeline."""
    try:
        import figures
        figures.generate(date)
    except Exception as exc:
        import warnings
        warnings.warn(f"[Figures] Generation skipped ({exc})", RuntimeWarning)


if __name__ == "__main__":
    sys.exit(main())
