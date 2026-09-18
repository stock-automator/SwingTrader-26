"""
Factor exposure & risk-adjusted performance analytics, via quantstats.

quantstats is an optional dependency (`backend/requirements-report.txt`) -
it pulls in a large plotting/reporting stack the API doesn't otherwise
need. This follows the same optional-extra convention `quant/backtest.py`'s
`save_tearsheet` already uses: import it lazily, and raise a clear
`RuntimeError` (not an import-time crash for every caller of this module)
if it isn't installed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

#: Daily bars -> annualisation factor, matching `quant/backtest.py`'s
#: `TRADING_DAYS_PER_YEAR` so the two modules' Sharpe/etc. agree.
TRADING_DAYS_PER_YEAR = 252


def _finite(value: object) -> float | None:
    """`float(value)` if finite, else `None` - never NaN/inf across the API
    boundary (see `quant/backtest.py`'s identical helper and its docstring
    for why)."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class FactorExposure:
    """Risk-adjusted performance and benchmark factor exposure for one
    strategy return series."""

    alpha_annual_pct: float | None
    beta: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    calmar_ratio: float | None
    tail_ratio: float | None

    def as_dict(self) -> dict:
        return {
            "alpha_annual_pct": self.alpha_annual_pct,
            "beta": self.beta,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "calmar_ratio": self.calmar_ratio,
            "tail_ratio": self.tail_ratio,
        }


def compute_factor_exposure(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> FactorExposure:
    """Alpha/Beta (vs. `benchmark_returns`), Sharpe, Sortino, Calmar, and
    Tail Ratio for `strategy_returns`, via quantstats.

    Args:
        strategy_returns: Periodic (e.g. daily) fractional returns.
        benchmark_returns: Same, for the benchmark (e.g. SPY) alpha/beta
            are measured against. Only the dates common to both series are
            used.
        risk_free_rate: Annualised risk-free rate, e.g. 0.04 for 4%.
        periods_per_year: Bars per year, for annualisation.

    Raises:
        RuntimeError: if quantstats is not installed.
        ValueError: if either series is empty, or the two share no dates.
    """
    try:
        import quantstats as qs
    except ImportError as exc:  # pragma: no cover - exercised by hand
        raise RuntimeError(
            "quantstats is required for factor exposure analytics: "
            "pip install -r backend/requirements-report.txt"
        ) from exc

    if strategy_returns.empty or benchmark_returns.empty:
        raise ValueError("strategy_returns and benchmark_returns must be non-empty")

    common = strategy_returns.index.intersection(benchmark_returns.index)
    if len(common) == 0:
        raise ValueError("strategy_returns and benchmark_returns share no dates")

    strategy_returns = strategy_returns.loc[common]
    benchmark_returns = benchmark_returns.loc[common]

    greeks = qs.stats.greeks(
        strategy_returns, benchmark_returns, periods=periods_per_year
    )

    return FactorExposure(
        alpha_annual_pct=_finite(greeks.get("alpha", float("nan")) * 100),
        beta=_finite(greeks.get("beta")),
        sharpe_ratio=_finite(
            qs.stats.sharpe(
                strategy_returns, rf=risk_free_rate, periods=periods_per_year
            )
        ),
        sortino_ratio=_finite(
            qs.stats.sortino(
                strategy_returns, rf=risk_free_rate, periods=periods_per_year
            )
        ),
        calmar_ratio=_finite(
            qs.stats.calmar(strategy_returns, periods=periods_per_year)
        ),
        tail_ratio=_finite(qs.stats.tail_ratio(strategy_returns)),
    )
