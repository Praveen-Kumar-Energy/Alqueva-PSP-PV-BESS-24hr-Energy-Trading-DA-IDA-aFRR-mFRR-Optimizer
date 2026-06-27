"""
figures — Alqueva trading system figure package.

All 9 figures are generated automatically at the end of every pipeline run.
Outputs overwrite figures/output/ so the folder always reflects the latest run.

Public API (called by run_production.py):
    figures.generate(date)

Outputs saved to figures/output/:
    fig01_dispatch_profile.png          DA net position + DA price
    fig02_soc_trajectory.png            BESS SoC (% of 2 MWh)
    fig03_revenue_waterfall.png         Revenue by market (DA/IDA+XBID/aFRR/mFRR/Imbalance)
    fig04_reserve_capacity.png          aFRR + mFRR capacity offered (MW up/dn)
    fig05_gate_position_comparison.png  Position evolution across all 5 trading gates
    fig06_intraday_reoptimisation.png   DA vs final committed position (IDA+XBID delta)
    fig07_psp_dispatch.png              PSP turbine/pump MW schedule vs DA price
    fig08_pv_bess_flow.png              PV disposition + BESS charge/discharge power
    ops_board.png                       10-panel summary dashboard

Quality rules permanently applied:
    - White background, dark text — clean professional look
    - Bold axis labels and tick labels on every figure
    - Font sizes scale proportionally with figure width
    - Line widths scale proportionally with figure width
    - Grid drawn behind data (set_axisbelow), alpha=0.3
    - Panel labels (a)(b)... on all individual figures
    - Thousands formatter on revenue axes
    - Units included in every axis label
"""
from __future__ import annotations

import sqlite3
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT   = Path(__file__).resolve().parent.parent
_DB_POS = _ROOT / "runtime" / "db" / "positions.db"
_DB_RT  = _ROOT / "runtime" / "db" / "realtime.db"
_DB_RES = _ROOT / "runtime" / "db" / "reserve.db"
_RPTS   = _ROOT / "runtime" / "reports"
_OUT    = Path(__file__).resolve().parent / "output"
_OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Colors — white background palette
# ---------------------------------------------------------------------------

_BG       = "#ffffff"
_PANEL_BG = "#f8f9fa"
_GRID_C   = "#dee2e6"
_TEXT     = "#212529"
_BLUE     = "#1f77b4"
_GREEN    = "#2ca02c"
_RED      = "#d62728"
_ORANGE   = "#ff7f0e"
_PURPLE   = "#9467bd"
_TEAL     = "#17becf"

# ---------------------------------------------------------------------------
# Font / line scaling
# ---------------------------------------------------------------------------

_REF_WIDTH = 7.16

def _scale(fig_width: float) -> float:
    return max(1.0, fig_width / _REF_WIDTH)

def _rcparams(fig_width: float) -> dict:
    s = _scale(fig_width)
    return {
        "figure.facecolor"  : _BG,
        "axes.facecolor"    : _PANEL_BG,
        "axes.edgecolor"    : "#000000",
        "axes.labelcolor"   : "#000000",
        "axes.titlecolor"   : "#000000",
        "axes.labelweight"  : "bold",
        "axes.linewidth"    : round(0.8 * s, 2),
        "xtick.color"       : "#000000",
        "ytick.color"       : "#000000",
        "text.color"        : _TEXT,
        "grid.color"        : _GRID_C,
        "grid.linestyle"    : "--",
        "grid.linewidth"    : round(0.5 * s, 2),
        "grid.alpha"        : 0.3,
        "legend.facecolor"  : _BG,
        "legend.edgecolor"  : _GRID_C,
        "font.family"       : "sans-serif",
        "font.size"         : round(9  * s, 1),
        "axes.labelsize"    : round(9  * s, 1),
        "axes.titlesize"    : round(9  * s, 1),
        "xtick.labelsize"   : round(8  * s, 1),
        "ytick.labelsize"   : round(8  * s, 1),
        "legend.fontsize"   : round(8  * s, 1),
        "lines.linewidth"   : round(1.5 * s, 2),
    }

def _polish(ax: plt.Axes, fig_width: float = _REF_WIDTH) -> None:
    s = _scale(fig_width)
    ax.set_axisbelow(True)
    for lbl in ax.get_xticklabels() + ax.get_yticklabels():
        lbl.set_fontweight("bold")
        lbl.set_fontsize(round(8 * s, 1))
        lbl.set_color("#000000")

def _panel(ax: plt.Axes, letter: str, fig_width: float = _REF_WIDTH) -> None:
    s = _scale(fig_width)
    ax.set_title(f"({letter})", loc="left",
                 fontsize=round(9 * s, 1), fontweight="bold", color=_TEXT)

def _eur_fmt(ax: plt.Axes) -> None:
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(
            lambda x, _: f"EUR {x/1e3:,.0f}k" if abs(x) >= 1000 else f"EUR {x:.0f}"
        )
    )

# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _pos(date: str) -> pd.DataFrame:
    con = sqlite3.connect(_DB_POS)
    df  = pd.read_sql("SELECT * FROM positions WHERE delivery_date=?",
                      con, params=(date,))
    con.close()
    return df

def _res(date: str) -> pd.DataFrame:
    con = sqlite3.connect(_DB_RES)
    df  = pd.read_sql("SELECT * FROM reserve WHERE delivery_date=?",
                      con, params=(date,))
    con.close()
    return df

def _rt(date: str) -> pd.DataFrame:
    con = sqlite3.connect(_DB_RT)
    df  = pd.read_sql("SELECT * FROM delivery WHERE delivery_date=?",
                      con, params=(date,))
    con.close()
    return df

def _dispatch_df(date: str) -> pd.DataFrame:
    """Read Dispatch_Hourly sheet from the Excel report.

    Sheet layout: row1=title, row2=group band, row3=column names, rows4-27=data.
    skiprows=2 skips title+group-band so row3 becomes the header automatically.
    """
    path = _RPTS / f"daily_report_{date}.xlsx"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_excel(path, sheet_name="Dispatch_Hourly", skiprows=2)
    except Exception:
        return pd.DataFrame()


def _summary(date: str) -> dict:
    """Read Summary_KPIs sheet. Returns {metric_name: float_value}."""
    path = _RPTS / f"daily_report_{date}.xlsx"
    if not path.exists():
        return {}
    try:
        # Sheet has 4 columns: Section, Metric, Value, Unit
        # Row 1 = title, Row 2 = header — skip both
        raw = pd.read_excel(path, sheet_name="Summary_KPIs", header=None, skiprows=2)
        out = {}
        for _, row in raw.iterrows():
            k = row.iloc[1] if len(row) > 1 else None   # Metric column
            v = row.iloc[2] if len(row) > 2 else None   # Value column
            if pd.notna(k) and pd.notna(v):
                try:
                    out[str(k).strip()] = float(v)
                except (ValueError, TypeError):
                    pass
        return out
    except Exception:
        return {}

# ---------------------------------------------------------------------------
# Save helper
# ---------------------------------------------------------------------------

def _save(fig: plt.Figure, name: str) -> None:
    fig.savefig(_OUT / name, dpi=600, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    print(f"    {name}")

# ---------------------------------------------------------------------------
# Individual figures
# ---------------------------------------------------------------------------

def _fig01(date: str) -> None:
    W = 12.0
    plt.rcParams.update(_rcparams(W))
    pos = _pos(date)
    if pos.empty:
        return
    da    = pos[pos["gate"] == "DA"].sort_values("hour")
    hours = da["hour"].values
    vol   = da["volume_mwh"].values
    price = da["price_eur_mwh"].values
    lw    = round(1.5 * _scale(W), 2)

    fig, ax1 = plt.subplots(figsize=(W, 5))
    fig.patch.set_facecolor(_BG)
    ax2 = ax1.twinx()
    ax1.bar(hours, vol, color=[_GREEN if v >= 0 else _RED for v in vol],
            alpha=0.85, width=0.7, label="Net position (MWh)")
    ax1.axhline(0, color="#000000", linewidth=1.0)
    ax2.plot(hours, price, color=_ORANGE, linewidth=lw,
             marker="o", markersize=round(4 * _scale(W), 1), label="DA price (EUR/MWh)")
    ax1.set_xlabel("Hour (h)", fontweight="bold")
    ax1.set_ylabel("Net position (MWh)", fontweight="bold")
    ax2.set_ylabel("DA price (EUR/MWh)", color=_ORANGE, fontweight="bold",
                   labelpad=10)
    ax2.tick_params(axis="y", colors=_ORANGE)
    ax1.set_xticks(hours[::2])
    ax1.set_axisbelow(True); ax1.grid(True, axis="y")
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2,
               loc="upper center", bbox_to_anchor=(0.5, -0.18),
               ncol=len(h1 + h2), framealpha=0.9, edgecolor=_GRID_C)
    _polish(ax1, W); _polish(ax2, W)
    _panel(ax1, "a", W)
    fig.suptitle(f"Dispatch Profile  {date}", fontweight="bold")
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.18)
    _save(fig, "fig01_dispatch_profile.png")


_BESS_CAP_MWH = 2.0   # BESS capacity (1 MW / 2 MWh)
_SOC_MIN_PCT  = 10.0  # 0.20 MWh / 2.0 MWh × 100 = 10 %
_SOC_MAX_PCT  = 95.0  # 1.90 MWh / 2.0 MWh × 100 = 95 %


def _fig02(date: str) -> None:
    W   = 12.0
    plt.rcParams.update(_rcparams(W))
    df  = _dispatch_df(date)
    if df.empty or "BESS_SOC_MWh" not in df.columns:
        return
    hours   = df["Hour"].astype(int).values if "Hour" in df.columns else np.arange(1, len(df) + 1)
    soc_mwh = df["BESS_SOC_MWh"].values
    soc_pct  = soc_mwh / _BESS_CAP_MWH * 100.0   # convert MWh → % of 2 MWh capacity
    # Step plot: SoC is constant within each hour, steps at the end of each hour
    sh  = list(hours) + [hours[-1] + 1]
    soc_step = list(soc_pct) + [soc_pct[-1]]
    lw  = round(1.5 * _scale(W), 2)

    fig, ax = plt.subplots(figsize=(W, 4))
    fig.patch.set_facecolor(_BG)
    ax.fill_between(sh, soc_step, alpha=0.25, color=_BLUE, step="post")
    ax.step(sh, soc_step, color=_BLUE, linewidth=lw,
            where="post", label="BESS SoC (%)")
    ax.axhline(_SOC_MIN_PCT, color=_RED,   linestyle=":", linewidth=lw * 0.7,
               label=f"Min SoC {_SOC_MIN_PCT:.0f}% (0.20 MWh)")
    ax.axhline(_SOC_MAX_PCT, color=_GREEN, linestyle=":", linewidth=lw * 0.7,
               label=f"Max SoC {_SOC_MAX_PCT:.0f}% (1.90 MWh)")
    ax.set_xlabel("Hour (h)", fontweight="bold")
    ax.set_ylabel("State of Charge (% of 2 MWh)", fontweight="bold")
    ax.set_ylim(0, 105); ax.set_xticks(hours[::2])
    ax.set_axisbelow(True); ax.grid(True)
    ax.legend(framealpha=0.9, edgecolor=_GRID_C)
    _panel(ax, "b", W); _polish(ax, W)
    fig.suptitle(f"BESS SoC Trajectory  {date}", fontweight="bold")
    fig.tight_layout()
    _save(fig, "fig02_soc_trajectory.png")


def _fig03(date: str) -> None:
    W = 10.0
    plt.rcParams.update(_rcparams(W))
    sm = _summary(date)
    # Keys match metric names written by summary_kpi_builder.py (Section 1)
    da_rev   = sm.get("DA energy revenue", 0.0)
    ida_rev  = sm.get("IDA incremental revenue", 0.0)
    afrr_rev = sm.get("aFRR capacity revenue", 0.0) + sm.get("aFRR activation revenue", 0.0)
    mfrr_rev = sm.get("mFRR capacity revenue", 0.0) + sm.get("mFRR activation revenue", 0.0)
    imb_rev  = sm.get("Imbalance settlement", 0.0)
    markets = ["DA", "IDA+XBID", "aFRR", "mFRR", "Imbalance"]
    values  = [da_rev, ida_rev, afrr_rev, mfrr_rev, imb_rev]
    total   = sum(values)

    fig, ax = plt.subplots(figsize=(W, 5))
    fig.patch.set_facecolor(_BG)
    running = 0.0
    bottoms_list = []
    for v in values:
        bottoms_list.append(running if v >= 0 else running + v)
        running += v
    b = ax.bar(markets, [abs(v) for v in values], bottom=bottoms_list,
               color=[_GREEN if v >= 0 else _RED for v in values],
               alpha=0.85, width=0.5)
    ax.bar(["TOTAL"], [total], color=_BLUE, alpha=0.85, width=0.5)
    ax.axhline(0, color="#000000", linewidth=1.0)
    fs = round(8 * _scale(W), 1)
    for bar, v in zip(b, values):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_y() + bar.get_height() / 2,
                f"EUR {v:,.0f}", ha="center", va="center",
                fontsize=fs, color=_TEXT, fontweight="bold")
    ax.text(len(markets), total / 2, f"EUR {total:,.0f}",
            ha="center", va="center", fontsize=fs, color=_TEXT, fontweight="bold")
    ax.set_ylabel("Revenue (EUR)", fontweight="bold")
    ax.set_axisbelow(True); ax.grid(True, axis="y")
    _eur_fmt(ax)
    _panel(ax, "c", W); _polish(ax, W)
    fig.suptitle(f"Revenue Waterfall by Market  {date}", fontweight="bold")
    fig.tight_layout()
    _save(fig, "fig03_revenue_waterfall.png")


def _fig04(date: str) -> None:
    W = 12.0
    plt.rcParams.update(_rcparams(W))
    res = _res(date)
    if res.empty:
        return
    hours = list(range(1, 25))
    lw    = round(0.8 * _scale(W), 2)
    fs9   = round(9 * _scale(W), 1)

    fig, axes = plt.subplots(2, 1, figsize=(W, 7), sharex=True)
    fig.patch.set_facecolor(_BG)
    for ax, prod, lbl, cu, cd in [
        (axes[0], "aFRR", "d", _GREEN, _RED),
        (axes[1], "mFRR", "e", _BLUE,  _ORANGE),
    ]:
        sub = res[res["product"] == prod].sort_values("hour")
        up  = sub.set_index("hour")["up_mw"].reindex(hours, fill_value=0).values
        dn  = sub.set_index("hour")["dn_mw"].reindex(hours, fill_value=0).values
        ax.bar(hours, up,  color=cu, alpha=0.75, width=0.4, label="Up (MW)",   align="edge")
        ax.bar(hours, -dn, color=cd, alpha=0.75, width=-0.4, label="Down (MW)", align="edge")
        ax.axhline(0, color="#000000", linewidth=1.0)
        ax.set_ylabel("Capacity (MW)", fontweight="bold")
        ax.set_axisbelow(True); ax.grid(True, axis="y")
        ax.legend(loc="upper right", framealpha=0.9, edgecolor=_GRID_C)
        _panel(ax, lbl, W); _polish(ax, W)
    axes[0].set_title(f"(d) aFRR Reserve Capacity  {date}", loc="left",
                      fontsize=fs9, fontweight="bold", color=_TEXT)
    axes[1].set_title(f"(e) mFRR Reserve Capacity  {date}", loc="left",
                      fontsize=fs9, fontweight="bold", color=_TEXT)
    axes[1].set_xlabel("Hour (h)", fontweight="bold")
    axes[1].set_xticks(hours[::2])
    fig.tight_layout()
    _save(fig, "fig04_reserve_capacity.png")


def _fig05(date: str) -> None:
    W = 12.0
    plt.rcParams.update(_rcparams(W))
    pos = _pos(date)
    if pos.empty:
        return
    hours = list(range(1, 25))
    lw    = round(1.5 * _scale(W), 2)
    ms    = round(4 * _scale(W), 1)

    fig, ax = plt.subplots(figsize=(W, 5))
    fig.patch.set_facecolor(_BG)
    for gate, col in [("DA", _BLUE), ("IDA1", _GREEN), ("IDA2", _ORANGE),
                      ("IDA3", _PURPLE), ("XBID", _TEAL)]:
        sub = pos[pos["gate"] == gate].sort_values("hour")
        if sub.empty:
            continue
        hv = sub.set_index("hour")["volume_mwh"].reindex(hours, fill_value=np.nan)
        ax.plot(hours, hv.values, color=col, linewidth=lw,
                marker=".", markersize=ms, label=gate, alpha=0.85)
    ax.axhline(0, color="#000000", linewidth=1.0)
    ax.set_xlabel("Hour (h)", fontweight="bold")
    ax.set_ylabel("Net position (MWh)", fontweight="bold")
    ax.set_xticks(hours[::2])
    ax.set_axisbelow(True); ax.grid(True)
    ax.legend(framealpha=0.9, edgecolor=_GRID_C)
    _panel(ax, "d", W); _polish(ax, W)
    fig.suptitle(f"Position by Gate (DA to XBID)  {date}", fontweight="bold")
    fig.tight_layout()
    _save(fig, "fig05_gate_position_comparison.png")


def _fig06(date: str) -> None:
    W = 12.0
    plt.rcParams.update(_rcparams(W))
    pos = _pos(date)
    if pos.empty:
        return
    hours = list(range(1, 25))
    lw    = round(1.5 * _scale(W), 2)
    da    = pos[pos["gate"] == "DA"].set_index("hour")["volume_mwh"].reindex(hours, fill_value=0)
    # Final committed position = DA + all intraday gates (IDA1, IDA2, IDA3, XBID)
    intraday = (pos[pos["gate"].isin(["IDA1", "IDA2", "IDA3", "XBID"])]
                .groupby("hour")["volume_mwh"].sum()
                .reindex(hours, fill_value=0))
    final = (da + intraday).values
    delta = final - da.values

    fig, ax = plt.subplots(figsize=(W, 5))
    fig.patch.set_facecolor(_BG)
    ax.step(hours, da.values, color=_BLUE,  linewidth=lw,
            where="mid", label="DA position (MWh)")
    ax.step(hours, final, color=_GREEN, linewidth=lw, linestyle="--",
            where="mid", label="Final committed (after IDA+XBID) (MWh)")
    ax.bar(hours, delta, color=[_GREEN if d >= 0 else _RED for d in delta],
           alpha=0.4, width=0.6, label="Intraday adjustment (MWh)")
    ax.axhline(0, color="#000000", linewidth=1.0)
    ax.set_xlabel("Hour (h)", fontweight="bold")
    ax.set_ylabel("Net position (MWh)", fontweight="bold")
    ax.set_xticks(hours[::2])
    ax.set_axisbelow(True); ax.grid(True, axis="y")
    ax.legend(framealpha=0.9, edgecolor=_GRID_C)
    _panel(ax, "e", W); _polish(ax, W)
    fig.suptitle(f"Intraday Re-optimisation Impact (DA → IDA+XBID)  {date}", fontweight="bold")
    fig.tight_layout()
    _save(fig, "fig06_intraday_reoptimisation.png")


# ---------------------------------------------------------------------------
# fig07 — PSP Unit Dispatch Schedule
# ---------------------------------------------------------------------------

_PSP_MAX_GEN_MW  = 518.4   # 4 × 129.6 MW turbines
_PSP_MAX_PUMP_MW = 446.4   # 4 × 111.6 MW pumps


def _fig07(date: str) -> None:
    """PSP turbine/pump dispatch vs DA price — core MILP output.

    Green bars  = turbine generation (MW, positive, up to 518.4 MW)
    Red bars    = pumping load (MW, plotted negative, down to -446.4 MW)
    Orange line = DA price (EUR/MWh, right axis)
    Dashed capacity limit lines show utilisation vs installed capacity.
    """
    W = 12.0
    plt.rcParams.update(_rcparams(W))
    df = _dispatch_df(date)
    if df.empty or "PSP_gen_MW" not in df.columns:
        return
    hours   = df["Hour"].astype(int).values if "Hour" in df.columns else np.arange(1, len(df) + 1)
    gen_mw  = df["PSP_gen_MW"].values
    pump_mw = df["PSP_pump_MW"].values
    price   = df["DA_price_EUR_MWh"].values if "DA_price_EUR_MWh" in df.columns else None
    lw      = round(1.5 * _scale(W), 2)
    ms      = round(4   * _scale(W), 1)
    bw      = 0.4   # bar half-width

    fig, ax1 = plt.subplots(figsize=(W, 5))
    fig.patch.set_facecolor(_BG)

    # Generation bars (positive)
    ax1.bar([h - bw / 2 for h in hours], gen_mw,
            width=bw, color=_GREEN, alpha=0.80, label="Turbine generation (MW)")
    # Pumping bars (negative — plotted downward)
    ax1.bar([h + bw / 2 for h in hours], -pump_mw,
            width=bw, color=_RED, alpha=0.80, label="Pump load (MW, −ve)")
    ax1.axhline(0, color="#000000", linewidth=1.0)
    # Capacity reference lines
    ax1.axhline( _PSP_MAX_GEN_MW,  color=_GREEN, linestyle=":", linewidth=lw * 0.6,
                label=f"Max gen {_PSP_MAX_GEN_MW} MW")
    ax1.axhline(-_PSP_MAX_PUMP_MW, color=_RED,   linestyle=":", linewidth=lw * 0.6,
                label=f"Max pump {_PSP_MAX_PUMP_MW} MW")

    ax1.set_xlabel("Hour (h)", fontweight="bold")
    ax1.set_ylabel("PSP power (MW)", fontweight="bold")
    ax1.set_xticks(hours[::2])
    ax1.set_axisbelow(True); ax1.grid(True, axis="y")

    # DA price — right axis
    if price is not None:
        ax2 = ax1.twinx()
        ax2.plot(hours, price, color=_ORANGE, linewidth=lw,
                 marker="o", markersize=ms, label="DA price (EUR/MWh)")
        ax2.set_ylabel("DA price (EUR/MWh)", color=_ORANGE, fontweight="bold", labelpad=10)
        ax2.tick_params(axis="y", colors=_ORANGE)
        for lbl in ax2.get_yticklabels():
            lbl.set_fontweight("bold")
        h2, l2 = ax2.get_legend_handles_labels()
    else:
        h2, l2 = [], []

    h1, l1 = ax1.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2,
               loc="upper center", bbox_to_anchor=(0.5, -0.18),
               ncol=len(h1 + h2), framealpha=0.9, edgecolor=_GRID_C)
    _panel(ax1, "g", W); _polish(ax1, W)
    fig.suptitle(
        f"PSP Dispatch Schedule  {date}  |  "
        f"4×129.6 MW turbines  /  4×111.6 MW pumps",
        fontweight="bold"
    )
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.18)
    _save(fig, "fig07_psp_dispatch.png")


# ---------------------------------------------------------------------------
# fig08 — PV + BESS Energy Flow
# ---------------------------------------------------------------------------

def _fig08(date: str) -> None:
    """PV generation breakdown + BESS charge/discharge power.

    Top panel: PV available (line), PV used (green fill), PV to BESS (teal fill),
               PV curtailed (orange fill) — stacked to show disposition of every MWh.
    Bottom panel: BESS discharge (green bars, +ve) and charge (red bars, −ve).
    Both panels share the hour axis.
    """
    W = 12.0
    plt.rcParams.update(_rcparams(W))
    df = _dispatch_df(date)
    if df.empty or "PV_used_MW" not in df.columns:
        return
    hours       = df["Hour"].astype(int).values if "Hour" in df.columns else np.arange(1, len(df) + 1)
    pv_avail    = df["PV_available_MW"].values   if "PV_available_MW"   in df.columns else np.zeros(len(hours))
    pv_used     = df["PV_used_MW"].values
    pv_to_bess  = df["PV_to_BESS_MW"].values    if "PV_to_BESS_MW"    in df.columns else np.zeros(len(hours))
    pv_curt     = df["PV_curtailed_MW"].values   if "PV_curtailed_MW"  in df.columns else np.zeros(len(hours))
    bess_dis    = df["BESS_discharge_MW"].values if "BESS_discharge_MW" in df.columns else np.zeros(len(hours))
    bess_chg    = df["BESS_total_charge_MW"].values if "BESS_total_charge_MW" in df.columns else np.zeros(len(hours))
    lw          = round(1.5 * _scale(W), 2)

    fig, (ax_pv, ax_bs) = plt.subplots(2, 1, figsize=(W, 7), sharex=True)
    fig.patch.set_facecolor(_BG)

    # ── Top: PV disposition (stacked bars) ──────────────────────────────────
    ax_pv.bar(hours, pv_used,    width=0.6, color=_GREEN,  alpha=0.85,
              label="PV → grid (used)")
    ax_pv.bar(hours, pv_to_bess, width=0.6, color=_TEAL,   alpha=0.85,
              bottom=pv_used, label="PV → BESS")
    ax_pv.bar(hours, pv_curt,    width=0.6, color=_ORANGE,  alpha=0.70,
              bottom=pv_used + pv_to_bess, label="PV curtailed")
    ax_pv.plot(hours, pv_avail, color=_RED, linewidth=lw, linestyle="--",
               marker=".", markersize=round(3 * _scale(W), 1),
               label="PV available (forecast)")
    ax_pv.set_ylabel("PV power (MW)", fontweight="bold")
    ax_pv.set_axisbelow(True); ax_pv.grid(True, axis="y")
    # PV legend — below top panel in single row
    hpv, lpv = ax_pv.get_legend_handles_labels()
    ax_pv.legend(hpv, lpv,
                 loc="upper center", bbox_to_anchor=(0.5, -0.12),
                 ncol=len(hpv), framealpha=0.9, edgecolor=_GRID_C)
    ax_pv.set_title("(g) PV Generation Disposition", loc="left",
                    fontsize=round(9 * _scale(W), 1), fontweight="bold")
    _polish(ax_pv, W)

    # ── Bottom: BESS power ──────────────────────────────────────────────────
    bw = 0.35
    ax_bs.bar([h - bw / 2 for h in hours],  bess_dis, width=bw,
              color=_GREEN, alpha=0.85, label="BESS discharge (MW)")
    ax_bs.bar([h + bw / 2 for h in hours], -bess_chg, width=bw,
              color=_RED,   alpha=0.85, label="BESS charge (MW, −ve)")
    ax_bs.axhline(0, color="#000000", linewidth=1.0)
    # 1 MW capacity reference lines
    ax_bs.axhline( 1.0, color=_GREEN, linestyle=":", linewidth=lw * 0.6,
                  label="Max discharge 1 MW")
    ax_bs.axhline(-1.0, color=_RED,   linestyle=":", linewidth=lw * 0.6,
                  label="Max charge 1 MW")
    ax_bs.set_xlabel("Hour (h)", fontweight="bold")
    ax_bs.set_ylabel("BESS power (MW)", fontweight="bold")
    ax_bs.set_xticks(hours[::2])
    ax_bs.set_axisbelow(True); ax_bs.grid(True, axis="y")
    # BESS legend — below bottom panel in single row
    hbs, lbs = ax_bs.get_legend_handles_labels()
    ax_bs.legend(hbs, lbs,
                 loc="upper center", bbox_to_anchor=(0.5, -0.18),
                 ncol=len(hbs), framealpha=0.9, edgecolor=_GRID_C)
    ax_bs.set_title("(h) BESS Charge / Discharge Power", loc="left",
                    fontsize=round(9 * _scale(W), 1), fontweight="bold")
    _polish(ax_bs, W)

    fig.suptitle(
        f"PV + BESS Energy Flow  {date}  |  5 MW PV  /  1 MW · 2 MWh BESS",
        fontweight="bold"
    )
    fig.tight_layout()
    fig.subplots_adjust(hspace=0.45, bottom=0.12)
    _save(fig, "fig08_pv_bess_flow.png")


# ---------------------------------------------------------------------------
# Ops board
# ---------------------------------------------------------------------------

def _ops_board(date: str) -> None:
    # Fixed font sizes — ops board panels are each ~6" wide regardless of
    # the overall 20" figure, so the full scaling formula over-sizes fonts.
    FT = 10   # panel title
    FL = 9    # axis label
    FK = 8    # tick / legend / KPI value
    LW = 2.0  # line width
    MS = 4    # marker size

    plt.rcParams.update({
        "figure.facecolor" : _BG, "axes.facecolor"  : _PANEL_BG,
        "axes.edgecolor"   : "#000000", "axes.labelcolor" : "#000000",
        "axes.titlecolor"  : "#000000", "axes.labelweight" : "bold",
        "axes.linewidth"   : 0.8, "xtick.color"      : "#000000",
        "ytick.color"      : "#000000", "text.color"       : _TEXT,
        "grid.color"       : _GRID_C, "grid.linestyle"   : "--",
        "grid.linewidth"   : 0.5, "grid.alpha"        : 0.3,
        "legend.facecolor" : _BG, "legend.edgecolor"  : _GRID_C,
        "font.family"      : "sans-serif",
        "font.size"        : FK, "axes.labelsize"    : FL,
        "axes.titlesize"   : FT, "xtick.labelsize"   : FK,
        "ytick.labelsize"  : FK, "legend.fontsize"   : FK,
        "lines.linewidth"  : LW,
    })

    pos  = _pos(date)
    res  = _res(date)
    sm   = _summary(date)
    disp = _dispatch_df(date)

    fig = plt.figure(figsize=(20, 13), facecolor=_BG)
    gs  = gridspec.GridSpec(3, 3, figure=fig,
                            hspace=0.60, wspace=0.50,
                            left=0.07, right=0.97,
                            top=0.91, bottom=0.07)
    hours = list(range(1, 25))

    def _px(ax):
        ax.set_axisbelow(True)
        for lbl in ax.get_xticklabels() + ax.get_yticklabels():
            lbl.set_fontweight("bold"); lbl.set_fontsize(FK); lbl.set_color("#000000")

    # --- (a) Dispatch + Price ---
    ax00 = fig.add_subplot(gs[0, 0])
    if not pos.empty:
        da = pos[pos["gate"] == "DA"].sort_values("hour")
        h, v, p = da["hour"].values, da["volume_mwh"].values, da["price_eur_mwh"].values
        ax00.bar(h, v, color=[_GREEN if x >= 0 else _RED for x in v], alpha=0.8, width=0.7)
        ax_p = ax00.twinx()
        # No ylabel on twinx — label goes into tick color instead to avoid overlap
        ax_p.plot(h, p, color=_ORANGE, linewidth=LW)
        ax_p.tick_params(axis="y", colors=_ORANGE, labelsize=FK)
        ax_p.yaxis.set_label_position("right")
        for lbl in ax_p.get_yticklabels():
            lbl.set_fontweight("bold")
        # Annotate with a small text note inside the panel instead of an axis label
        ax_p.annotate("Price (EUR/MWh)", xy=(0.98, 0.97), xycoords="axes fraction",
                      ha="right", va="top", fontsize=FK - 1,
                      color=_ORANGE, fontweight="bold",
                      bbox=dict(boxstyle="round,pad=0.2", fc=_BG, ec=_ORANGE, alpha=0.7))
    ax00.set_title("(a) DA Dispatch + Price", loc="left", fontsize=FT, fontweight="bold")
    ax00.set_xlabel("Hour (h)", fontsize=FL, fontweight="bold")
    ax00.set_ylabel("Net position (MWh)", fontsize=FL, fontweight="bold")
    ax00.set_axisbelow(True); ax00.grid(True, axis="y"); _px(ax00)

    # --- (b) SoC — read BESS_SOC_MWh from Dispatch_Hourly, convert to % ---
    ax01 = fig.add_subplot(gs[0, 1])
    if not disp.empty and "BESS_SOC_MWh" in disp.columns:
        h_d  = disp["Hour"].astype(int).values if "Hour" in disp.columns else np.arange(1, len(disp) + 1)
        soc_pct = disp["BESS_SOC_MWh"].values / _BESS_CAP_MWH * 100.0
        sh = list(h_d) + [int(h_d[-1]) + 1]
        soc_step = list(soc_pct) + [soc_pct[-1]]
        ax01.fill_between(sh, soc_step, alpha=0.25, color=_BLUE, step="post")
        ax01.step(sh, soc_step, color=_BLUE, linewidth=LW, where="post")
        ax01.axhline(_SOC_MIN_PCT, color=_RED,   linestyle=":", linewidth=1.2,
                     label=f"Min {_SOC_MIN_PCT:.0f}%")
        ax01.axhline(_SOC_MAX_PCT, color=_GREEN, linestyle=":", linewidth=1.2,
                     label=f"Max {_SOC_MAX_PCT:.0f}%")
        ax01.legend(fontsize=FK, framealpha=0.9, edgecolor=_GRID_C, loc="lower right")
    ax01.set_title("(b) BESS SoC (% of 2 MWh)", loc="left", fontsize=FT, fontweight="bold")
    ax01.set_xlabel("Hour (h)", fontsize=FL, fontweight="bold")
    ax01.set_ylabel("SoC (%)", fontsize=FL, fontweight="bold")
    ax01.set_ylim(0, 105)
    ax01.set_axisbelow(True); ax01.grid(True); _px(ax01)

    # --- KPI tiles: label left, value right, one row per KPI ---
    ax02 = fig.add_subplot(gs[0, 2])
    ax02.axis("off")
    ax02.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax02.transAxes,
                                  facecolor=_PANEL_BG, edgecolor=_GRID_C,
                                  linewidth=1.2, zorder=0))
    ax02.text(0.5, 0.97, "KPIs", transform=ax02.transAxes,
              fontsize=FT, fontweight="bold", color=_TEXT,
              ha="center", va="top")
    if sm:
        kpis = [
            ("TOTAL P&L",  sm.get("Total daily P&L", 0),  "EUR"),
            ("DA P&L",     sm.get("DA energy revenue", 0), "EUR"),
            ("Reserves",   sm.get("aFRR capacity revenue", 0) + sm.get("aFRR activation revenue", 0)
                         + sm.get("mFRR capacity revenue", 0) + sm.get("mFRR activation revenue", 0), "EUR"),
            ("Spread",     sm.get("Price spread captured", 0), "EUR/MWh"),
            ("Reserve %",  sm.get("Reserve share of P&L", 0), "%"),
        ]
        y0, gap = 0.84, 0.155
        for i, (label, val, unit) in enumerate(kpis):
            col = _GREEN if val >= 0 else _RED
            y   = y0 - i * gap
            ax02.text(0.04, y, label, transform=ax02.transAxes,
                      fontsize=FK, color=_TEXT, va="center", fontweight="bold")
            ax02.text(0.96, y, f"{val:,.0f} {unit}", transform=ax02.transAxes,
                      fontsize=FK, color=col, va="center", ha="right", fontweight="bold")
            # Separator line
            ax02.plot([0.04, 0.96], [y - gap * 0.45, y - gap * 0.45],
                      color=_GRID_C, linewidth=0.6, transform=ax02.transAxes,
                      clip_on=False)

    # --- (c) Position evolution ---
    ax10 = fig.add_subplot(gs[1, :2])
    if not pos.empty:
        for gate, col in [("DA", _BLUE), ("IDA1", _GREEN), ("IDA2", _ORANGE),
                          ("IDA3", _PURPLE), ("XBID", _TEAL)]:
            sub = pos[pos["gate"] == gate].sort_values("hour")
            if sub.empty:
                continue
            hv = sub.set_index("hour")["volume_mwh"].reindex(hours, fill_value=np.nan)
            ax10.plot(hours, hv.values, color=col, linewidth=LW,
                      marker=".", markersize=MS, label=gate, alpha=0.9)
    ax10.axhline(0, color="#000000", linewidth=1.0)
    ax10.set_title("(c) Position Evolution: DA  IDA1  IDA2  IDA3  XBID",
                   loc="left", fontsize=FT, fontweight="bold")
    ax10.set_xlabel("Hour (h)", fontsize=FL, fontweight="bold")
    ax10.set_ylabel("Net position (MWh)", fontsize=FL, fontweight="bold")
    ax10.set_xticks(hours[::2])
    ax10.set_axisbelow(True); ax10.grid(True)
    ax10.legend(fontsize=FK, ncol=5, loc="upper right",
                framealpha=0.9, edgecolor=_GRID_C)
    _px(ax10)

    # --- (d) aFRR ---
    ax12 = fig.add_subplot(gs[1, 2])
    if not res.empty:
        afrr = res[res["product"] == "aFRR"].sort_values("hour")
        up   = afrr.set_index("hour")["up_mw"].reindex(hours, fill_value=0).values
        dn   = afrr.set_index("hour")["dn_mw"].reindex(hours, fill_value=0).values
        ax12.bar(hours, up,  color=_GREEN, alpha=0.7, width=0.4, label="Up",   align="edge")
        ax12.bar(hours, -dn, color=_RED,   alpha=0.7, width=-0.4, label="Down", align="edge")
        ax12.axhline(0, color="#000000", linewidth=1.0)
    ax12.set_title("(d) aFRR Capacity (MW)", loc="left", fontsize=FT, fontweight="bold")
    ax12.set_xlabel("Hour (h)", fontsize=FL, fontweight="bold")
    ax12.set_ylabel("Capacity (MW)", fontsize=FL, fontweight="bold")
    ax12.set_axisbelow(True); ax12.grid(True, axis="y")
    ax12.legend(fontsize=FK, framealpha=0.9, edgecolor=_GRID_C)
    _px(ax12)

    # --- (e) mFRR ---
    ax20 = fig.add_subplot(gs[2, :2])
    if not res.empty:
        mfrr = res[res["product"] == "mFRR"].sort_values("hour")
        up   = mfrr.set_index("hour")["up_mw"].reindex(hours, fill_value=0).values
        dn   = mfrr.set_index("hour")["dn_mw"].reindex(hours, fill_value=0).values
        ax20.bar(hours, up,  color=_BLUE,   alpha=0.75, width=0.4, label="Up",   align="edge")
        ax20.bar(hours, -dn, color=_ORANGE, alpha=0.75, width=-0.4, label="Down", align="edge")
        ax20.axhline(0, color="#000000", linewidth=1.0)
    ax20.set_title("(e) mFRR Capacity (MW)", loc="left", fontsize=FT, fontweight="bold")
    ax20.set_xlabel("Hour (h)", fontsize=FL, fontweight="bold")
    ax20.set_ylabel("Capacity (MW)", fontsize=FL, fontweight="bold")
    ax20.set_xticks(hours[::2])
    ax20.set_axisbelow(True); ax20.grid(True, axis="y")
    ax20.legend(fontsize=FK, framealpha=0.9, edgecolor=_GRID_C)
    _px(ax20)

    # --- (f) P&L waterfall ---
    ax22 = fig.add_subplot(gs[2, 2])
    markets = ["DA", "IDA+XBID", "aFRR", "mFRR", "Imbalance"]
    if sm:
        values = [
            sm.get("DA energy revenue", 0.0),
            sm.get("IDA incremental revenue", 0.0),
            sm.get("aFRR capacity revenue", 0.0) + sm.get("aFRR activation revenue", 0.0),
            sm.get("mFRR capacity revenue", 0.0) + sm.get("mFRR activation revenue", 0.0),
            sm.get("Imbalance settlement", 0.0),
        ]
    else:
        values = [0.0] * 5
    running = 0.0
    for i, (m, v) in enumerate(zip(markets, values)):
        bot = running if v >= 0 else running + v
        ax22.bar(i, abs(v), bottom=bot,
                 color=_GREEN if v >= 0 else _RED, alpha=0.8, width=0.6)
        running += v
    ax22.set_xticks(range(len(markets)))
    ax22.set_xticklabels(markets, rotation=35, ha="right", fontsize=FK)
    ax22.set_title("(f) P&L Waterfall", loc="left", fontsize=FT, fontweight="bold")
    ax22.set_ylabel("Revenue (EUR)", fontsize=FL, fontweight="bold")
    ax22.set_axisbelow(True); ax22.grid(True, axis="y")
    _eur_fmt(ax22); _px(ax22)

    fig.suptitle(
        f"Alqueva PSP+PV+BESS  |  Trading Ops Board  |  {date}",
        fontsize=14, fontweight="bold", color=_TEXT, y=0.975
    )
    _save(fig, "ops_board.png")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate(date: str) -> None:
    """Generate all 9 figures for the given delivery date. Overwrites previous output."""
    print("  Generating figures...")
    _fig01(date)
    _fig02(date)
    _fig03(date)
    _fig04(date)
    _fig05(date)
    _fig06(date)
    _fig07(date)
    _fig08(date)
    _ops_board(date)
    print(f"  Figures saved -> figures/output/  ({len(list(_OUT.glob('*.png')))} files)\n")
