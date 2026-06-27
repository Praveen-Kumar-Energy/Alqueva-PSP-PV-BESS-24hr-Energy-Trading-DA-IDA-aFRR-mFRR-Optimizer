"""
activation_ramp_tracker.py — FAT ramp trajectory simulation for aFRR / mFRR.

The Full Activation Time (FAT) is the maximum allowed time to reach the full
activated MW after the TSO signal arrives. During the ramp-up window, energy
delivery is LESS than face value. This module:

  1. Simulates the per-minute power trajectory within a single ISP.
  2. Computes the EFFECTIVE energy actually delivered (ramp-corrected).
  3. Checks FAT compliance: does the plant reach >= threshold_pct of target
     within fat_min minutes?

Physical model
--------------
  * BESS responds INSTANTLY (+ bess_power_mw from t=0).
  * PSP ramps linearly from 0 up to (target - bess_contribution) at
    total_ramp_mw_per_min. After that, output is held flat.
  * Energy = area under the power-time curve (trapezoid + plateau).

Effective ISP energy formula
-----------------------------
  For a linear ramp from 0 to target_mw in fat_min, then plateau:
    eff_isp_h = (isp_duration_min - fat_min / 2) / 60

  aFRR: eff = (15 - 2.5) / 60 = 0.2083 h  vs face 0.25 h  (-16.7%)
  mFRR: eff = (15 - 6.25) / 60 = 0.1458 h vs face 0.25 h  (-41.7%)

This correction is applied in reserve_activation.py to energy booking and
settlement so activation revenue reflects actual MWh delivered, not face value.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class RampTrajectory:
    """Minute-by-minute power trajectory within one ISP activation."""
    target_mw: float
    fat_min: float
    isp_duration_min: float
    minutes: List[float]      # 0..isp_duration_min
    power_mw: List[float]     # power at each minute
    energy_mwh: float         # total energy (integral of power over time)
    fat_compliant: bool       # True if >= threshold_pct of target reached within fat_min
    time_to_95pct_min: float  # minutes until 95% of target reached (inf if never)


def simulate_ramp_trajectory(
    target_mw: float,
    fat_min: float,
    isp_duration_min: float,
    total_ramp_mw_per_min: float,
    bess_power_mw: float = 0.0,
    resolution_min: float = 0.5,
    compliance_threshold: float = 0.95,
) -> RampTrajectory:
    """Simulate the power ramp trajectory for one activation.

    Returns minute-by-minute power from t=0 to t=isp_duration_min,
    total energy (MWh), FAT compliance, and time to 95% of target.

    Parameters
    ----------
    target_mw               : total MW to deliver (BESS + PSP)
    fat_min                 : Full Activation Time (minutes)
    isp_duration_min        : ISP length (minutes)
    total_ramp_mw_per_min   : PSP fleet ramp rate (MW/min)
    bess_power_mw           : BESS contribution (instantaneous from t=0)
    resolution_min          : simulation time step (minutes)
    compliance_threshold    : fraction of target considered "reached"
    """
    bess_contrib = min(bess_power_mw, target_mw)
    psp_target = max(0.0, target_mw - bess_contrib)

    minutes: List[float] = []
    power_mw: List[float] = []

    t = 0.0
    time_to_95pct = float("inf")
    threshold_mw = target_mw * compliance_threshold

    while t <= isp_duration_min + 1e-9:
        psp_ramp = min(psp_target, total_ramp_mw_per_min * t)
        p = bess_contrib + psp_ramp
        p = min(p, target_mw)    # cap at target (can't overshoot)
        minutes.append(round(t, 3))
        power_mw.append(round(p, 4))
        if p >= threshold_mw - 1e-6 and time_to_95pct == float("inf"):
            time_to_95pct = t
        t += resolution_min

    # Energy = trapezoid integration over time steps.
    energy_wh = 0.0
    for i in range(len(minutes) - 1):
        dt_h = (minutes[i + 1] - minutes[i]) / 60.0
        energy_wh += 0.5 * (power_mw[i] + power_mw[i + 1]) * dt_h
    energy_mwh = energy_wh

    fat_compliant = time_to_95pct <= fat_min + 1e-6

    return RampTrajectory(
        target_mw=target_mw,
        fat_min=fat_min,
        isp_duration_min=isp_duration_min,
        minutes=minutes,
        power_mw=power_mw,
        energy_mwh=energy_mwh,
        fat_compliant=fat_compliant,
        time_to_95pct_min=time_to_95pct,
    )


def effective_energy_mwh(target_mw: float, fat_min: float,
                          isp_duration_min: float) -> float:
    """Ramp-corrected energy for one activation (analytical formula).

    Assumes linear ramp from 0 to target in fat_min, then flat plateau.
    Energy = triangle + rectangle = target × (fat/2 + (isp - fat)) / 60
           = target × (isp - fat/2) / 60
    """
    return target_mw * (isp_duration_min - fat_min / 2.0) / 60.0


def effective_isp_hours(fat_min: float, isp_duration_min: float) -> float:
    """Effective ISP duration in hours accounting for ramp-up energy loss.

    Multiply activated_mw by this instead of isp_h = isp_duration_min/60
    to get the actual energy delivered including ramp-up period.
    """
    return (isp_duration_min - fat_min / 2.0) / 60.0


def batch_compliance_check(
    activations: list,          # from ActivationStore.load()
    fat_min: float,
    isp_duration_min: float,
    total_ramp_mw_per_min: float,
    bess_power_mw: float = 0.0,
) -> dict:
    """Check FAT compliance for a batch of activation rows.

    Returns:
        {
          "n_activations": int,
          "n_compliant": int,
          "compliance_pct": float,
          "mean_time_to_95pct_min": float,
          "max_time_to_95pct_min": float,
          "non_compliant_isps": [isp, ...],
        }
    """
    n_compliant = 0
    times = []
    non_compliant = []

    for a in activations:
        direction_mw = a["up_mw"] if a["up_mw"] > 1e-6 else a["dn_mw"]
        if direction_mw < 1e-6:
            continue
        traj = simulate_ramp_trajectory(
            target_mw=direction_mw,
            fat_min=fat_min,
            isp_duration_min=isp_duration_min,
            total_ramp_mw_per_min=total_ramp_mw_per_min,
            bess_power_mw=bess_power_mw,
        )
        if traj.fat_compliant:
            n_compliant += 1
        else:
            non_compliant.append(a["isp"])
        times.append(traj.time_to_95pct_min if traj.time_to_95pct_min < float("inf")
                     else isp_duration_min)

    n = len(times)
    return {
        "n_activations": n,
        "n_compliant": n_compliant,
        "compliance_pct": 100.0 * n_compliant / n if n > 0 else 100.0,
        "mean_time_to_95pct_min": sum(times) / n if n > 0 else 0.0,
        "max_time_to_95pct_min": max(times) if times else 0.0,
        "non_compliant_isps": non_compliant,
    }
