"""
portfolio_risk_metrics.py — VaR, CVaR, Sharpe, Max Drawdown.

Historical simulation + Monte Carlo bootstrap over the daily P&L series
produced by the Phase 6 backtester. Standard risk metrics for energy
trading portfolio reporting.

Definitions
-----------
VaR(alpha)   : alpha-th lower-tail percentile of the P&L distribution.
               VaR(95%) = 5th-percentile daily P&L.
               "On the worst 5% of days we earn at most this amount."
CVaR(alpha)  : Mean P&L of all days at or below VaR(alpha).
               Also called Expected Shortfall (ES).
               "Given we are in the worst 5%, the average outcome is this."
Bootstrap    : Resample the P&L series with replacement n_samples times;
               compute VaR and CVaR from each resample.  The resulting
               mean ± std is the confidence interval on the risk estimate.
               Required because a 30-day history is a small sample.
Sharpe       : Annualised = (mean_daily_pnl / std_daily_pnl) * sqrt(252).
Max Drawdown : Largest peak-to-trough decline in the cumulative P&L curve.

All calculations are pure Python (no numpy/scipy) so there are no extra
dependencies beyond the existing requirements.txt.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List


@dataclass
class RiskMetrics:
    n_days: int
    mean_pnl_eur: float
    std_pnl_eur: float
    min_pnl_eur: float
    max_pnl_eur: float
    # Historical simulation
    var_95_eur: float        # 5th-percentile P&L
    cvar_95_eur: float       # mean of days at/below VaR(95%)
    var_99_eur: float        # 1st-percentile P&L
    cvar_99_eur: float       # mean of days at/below VaR(99%)
    # Monte Carlo bootstrap confidence intervals on VaR(95%) / CVaR(95%)
    var_95_mean: float
    var_95_std: float
    cvar_95_mean: float
    cvar_95_std: float
    # Risk-adjusted / drawdown
    sharpe_ratio: float
    max_drawdown_eur: float


def historical_var(pnl: List[float], alpha: float = 0.95) -> float:
    """Return the (1-alpha)-th lower percentile of the P&L series."""
    if not pnl:
        return 0.0
    sorted_pnl = sorted(pnl)
    # index into the lower tail: alpha=0.95 → idx ≈ 5th percentile
    idx = max(int((1.0 - alpha) * len(sorted_pnl)), 0)
    return sorted_pnl[idx]


def historical_cvar(pnl: List[float], alpha: float = 0.95) -> float:
    """Return CVaR (Expected Shortfall): mean of P&L days at or below VaR(alpha)."""
    if not pnl:
        return 0.0
    var = historical_var(pnl, alpha)
    tail = [x for x in pnl if x <= var]
    return sum(tail) / len(tail) if tail else var


def bootstrap_var_cvar(
    pnl: List[float],
    n_samples: int = 10_000,
    alpha: float = 0.95,
    seed: int = 42,
) -> dict:
    """
    Monte Carlo bootstrap confidence interval on VaR(alpha) and CVaR(alpha).

    Resample pnl with replacement n_samples times; compute VaR and CVaR
    from each resample. Returns the mean and standard deviation across all
    resamples — these are the confidence intervals on the risk estimates.

    Parameters
    ----------
    pnl       : daily P&L values (EUR)
    n_samples : number of bootstrap resamples (default 10,000)
    alpha     : confidence level (default 0.95 = 95% VaR/CVaR)
    seed      : random seed for reproducibility

    Returns
    -------
    dict with keys: var_mean, var_std, cvar_mean, cvar_std  (all EUR)
    """
    if len(pnl) < 2:
        return {"var_mean": 0.0, "var_std": 0.0,
                "cvar_mean": 0.0, "cvar_std": 0.0}

    rng = random.Random(seed)
    n = len(pnl)
    vars_: List[float] = []
    cvars_: List[float] = []

    for _ in range(n_samples):
        sample = [rng.choice(pnl) for _ in range(n)]
        vars_.append(historical_var(sample, alpha))
        cvars_.append(historical_cvar(sample, alpha))

    var_mean  = sum(vars_)  / n_samples
    cvar_mean = sum(cvars_) / n_samples
    var_std   = (sum((v - var_mean)  ** 2 for v in vars_)  / n_samples) ** 0.5
    cvar_std  = (sum((c - cvar_mean) ** 2 for c in cvars_) / n_samples) ** 0.5

    return {
        "var_mean":  var_mean,
        "var_std":   var_std,
        "cvar_mean": cvar_mean,
        "cvar_std":  cvar_std,
    }


def sharpe_ratio(pnl: List[float], trading_days_per_year: int = 252) -> float:
    """
    Annualised Sharpe ratio from daily P&L.

    Sharpe = (mean_daily / std_daily) * sqrt(252)

    Note: assumes risk-free rate = 0 (standard for short-term energy trading).
    """
    if len(pnl) < 2:
        return 0.0
    n = len(pnl)
    mean = sum(pnl) / n
    std = (sum((x - mean) ** 2 for x in pnl) / (n - 1)) ** 0.5
    if std < 1e-6:
        return 0.0
    return (mean / std) * (trading_days_per_year ** 0.5)


def max_drawdown(pnl: List[float]) -> float:
    """
    Maximum peak-to-trough decline in cumulative P&L (returned as a positive number).

    Walks the cumulative P&L series; tracks the running peak and records
    the largest drop from any peak to any subsequent trough.
    """
    if not pnl:
        return 0.0
    cumulative: List[float] = []
    running = 0.0
    for p in pnl:
        running += p
        cumulative.append(running)

    peak = cumulative[0]
    max_dd = 0.0
    for val in cumulative:
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd
    return max_dd


def compute_risk_metrics(
    pnl: List[float],
    n_bootstrap: int = 10_000,
) -> RiskMetrics:
    """
    Compute all risk metrics from a list of daily P&L values (EUR).

    Called by backtest_runner after the full backtest loop completes.
    n_bootstrap controls Monte Carlo resample count (10,000 default).
    """
    n = len(pnl)
    if n == 0:
        return RiskMetrics(0, 0.0, 0.0, 0.0, 0.0,
                           0.0, 0.0, 0.0, 0.0,
                           0.0, 0.0, 0.0, 0.0,
                           0.0, 0.0)

    mean = sum(pnl) / n
    std  = (sum((x - mean) ** 2 for x in pnl) / max(n - 1, 1)) ** 0.5
    boot = bootstrap_var_cvar(pnl, n_samples=n_bootstrap)

    return RiskMetrics(
        n_days          = n,
        mean_pnl_eur    = round(mean, 2),
        std_pnl_eur     = round(std, 2),
        min_pnl_eur     = round(min(pnl), 2),
        max_pnl_eur     = round(max(pnl), 2),
        var_95_eur      = round(historical_var(pnl, 0.95), 2),
        cvar_95_eur     = round(historical_cvar(pnl, 0.95), 2),
        var_99_eur      = round(historical_var(pnl, 0.99), 2),
        cvar_99_eur     = round(historical_cvar(pnl, 0.99), 2),
        var_95_mean     = round(boot["var_mean"],  2),
        var_95_std      = round(boot["var_std"],   2),
        cvar_95_mean    = round(boot["cvar_mean"], 2),
        cvar_95_std     = round(boot["cvar_std"],  2),
        sharpe_ratio    = round(sharpe_ratio(pnl), 4),
        max_drawdown_eur= round(max_drawdown(pnl), 2),
    )
