"""
Walk-forward robustness testing: rolling in-sample/out-of-sample windows,
Walk-Forward Efficiency, and parameter-sensitivity ("cliff") detection.

A backtest's headline return is measured on the same data a strategy's
parameters were (implicitly, by the researcher picking them) fit to.
Walk-forward analysis instead slides an in-sample (IS) window and an
adjacent, later out-of-sample (OOS) window across history together,
scoring the strategy only on OOS bars each time - the return an IS-only
analysis can never see, because the strategy had no way to have "known"
that window yet. Walk-Forward Efficiency (OOS return / IS return) close to
1.0 says the strategy's edge generalises; well below it says the backtest
was fitting noise.

Parameter sensitivity does the complementary check on a single edge of
that same question: does the strategy's performance hold up for parameter
values *near* the one chosen, or does it fall off a cliff a small nudge
away - the signature of a parameter tuned to this exact dataset rather
than a stable, tradeable regime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import pandas as pd

from .engine import run_backtest
from .metrics import compute_metrics
from .risk import RiskManager
from .strategies.base import BaseStrategy

#: Perturbations applied to a baseline parameter value, as fractions -
#: the product's "+/-20%" neighbor-bounds sensitivity sweep, plus the
#: baseline itself and a +/-10% midpoint for a smoother reading.
DEFAULT_PARAMETER_PERTURBATIONS: tuple[float, ...] = (-0.2, -0.1, 0.0, 0.1, 0.2)


def _window_return_pct(
    strategy: BaseStrategy,
    df_window: pd.DataFrame,
    initial_capital: float,
    risk_per_trade_pct: float,
    commission: float,
    slippage_pct: float,
) -> float | None:
    """Total return over `df_window`, or `None` if it's too short to
    backtest at all or the run otherwise fails - both treated as "no
    usable signal from this window" rather than raised, since a
    rolling scan is expected to hit unusable windows at its edges."""
    if len(df_window) < 2:
        return None

    risk_manager = RiskManager(
        account_equity=initial_capital, risk_per_trade_pct=risk_per_trade_pct
    )
    try:
        result = run_backtest(
            strategy,
            df_window,
            risk_manager,
            commission=commission,
            slippage_pct=slippage_pct,
        )
    except Exception:
        return None

    equity = result.equity_curve["Equity"]
    if equity.empty:
        return None

    start, end = float(equity.iloc[0]), float(equity.iloc[-1])
    if start <= 0:
        return None
    return (end / start - 1) * 100


def generate_windows(
    index: pd.DatetimeIndex,
    is_months: int,
    oos_months: int,
    step_months: int | None = None,
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """Rolling `(is_start, is_end, oos_start, oos_end)` windows spanning
    `index`, stepping forward by `step_months` (default: `oos_months`, so
    OOS windows tile the data with no overlap and no gap).

    Raises:
        ValueError: if `is_months`, `oos_months`, or `step_months` is not
            positive, or `index` is empty.
    """
    if is_months < 1:
        raise ValueError("is_months must be positive")
    if oos_months < 1:
        raise ValueError("oos_months must be positive")
    if step_months is not None and step_months < 1:
        raise ValueError("step_months must be positive")
    if len(index) == 0:
        raise ValueError("index must be non-empty")

    step_months = step_months or oos_months
    data_start, data_end = index.min(), index.max()

    windows = []
    is_start = data_start
    while True:
        is_end = is_start + pd.DateOffset(months=is_months)
        oos_start = is_end
        oos_end = oos_start + pd.DateOffset(months=oos_months)
        if oos_end > data_end:
            break
        windows.append((is_start, is_end, oos_start, oos_end))
        is_start = is_start + pd.DateOffset(months=step_months)

    return windows


@dataclass(frozen=True)
class WalkForwardWindow:
    """One IS/OOS pair's result."""

    is_start: pd.Timestamp
    is_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp
    is_return_pct: float | None
    oos_return_pct: float | None

    @property
    def efficiency(self) -> float | None:
        """Walk-Forward Efficiency: OOS return / IS return.

        `None` if either return is undefined, or the IS return is too
        close to zero for the ratio to be meaningful (it would blow up
        toward +/-infinity for an arbitrarily small OOS return).
        """
        if self.is_return_pct is None or self.oos_return_pct is None:
            return None
        if abs(self.is_return_pct) < 1e-9:
            return None
        return self.oos_return_pct / self.is_return_pct

    def as_dict(self) -> dict:
        return {
            "is_start": self.is_start.strftime("%Y-%m-%d"),
            "is_end": self.is_end.strftime("%Y-%m-%d"),
            "oos_start": self.oos_start.strftime("%Y-%m-%d"),
            "oos_end": self.oos_end.strftime("%Y-%m-%d"),
            "is_return_pct": self.is_return_pct,
            "oos_return_pct": self.oos_return_pct,
            "efficiency": self.efficiency,
        }


@dataclass(frozen=True)
class WalkForwardResult:
    windows: list[WalkForwardWindow]
    is_window_months: int
    oos_window_months: int

    @property
    def mean_efficiency(self) -> float | None:
        values = [w.efficiency for w in self.windows if w.efficiency is not None]
        return sum(values) / len(values) if values else None

    @property
    def median_efficiency(self) -> float | None:
        values = sorted(w.efficiency for w in self.windows if w.efficiency is not None)
        if not values:
            return None
        mid = len(values) // 2
        if len(values) % 2:
            return values[mid]
        return (values[mid - 1] + values[mid]) / 2

    def as_dict(self) -> dict:
        return {
            "is_window_months": self.is_window_months,
            "oos_window_months": self.oos_window_months,
            "mean_efficiency": self.mean_efficiency,
            "median_efficiency": self.median_efficiency,
            "windows": [w.as_dict() for w in self.windows],
        }


def run_walk_forward(
    strategy: BaseStrategy,
    df: pd.DataFrame,
    is_months: int = 12,
    oos_months: int = 3,
    step_months: int | None = None,
    initial_capital: float = 1000.0,
    risk_per_trade_pct: float = 0.02,
    commission: float = 0.001,
    slippage_pct: float = 0.0005,
) -> WalkForwardResult:
    """Roll `is_months`-in-sample / `oos_months`-out-of-sample windows
    across `df`, scoring `strategy` on each.

    Raises:
        ValueError: via `generate_windows`, for a non-positive window
            argument or an empty `df`.
    """
    windows = generate_windows(df.index, is_months, oos_months, step_months)

    results = []
    for is_start, is_end, oos_start, oos_end in windows:
        is_return = _window_return_pct(
            strategy,
            df.loc[is_start:is_end],
            initial_capital,
            risk_per_trade_pct,
            commission,
            slippage_pct,
        )
        oos_return = _window_return_pct(
            strategy,
            df.loc[oos_start:oos_end],
            initial_capital,
            risk_per_trade_pct,
            commission,
            slippage_pct,
        )
        results.append(
            WalkForwardWindow(
                is_start=is_start,
                is_end=is_end,
                oos_start=oos_start,
                oos_end=oos_end,
                is_return_pct=is_return,
                oos_return_pct=oos_return,
            )
        )

    return WalkForwardResult(
        windows=results, is_window_months=is_months, oos_window_months=oos_months
    )


@dataclass(frozen=True)
class ParameterSensitivityPoint:
    param_value: float
    return_pct: float | None
    sharpe_ratio: float | None

    def as_dict(self) -> dict:
        return {
            "param_value": self.param_value,
            "return_pct": self.return_pct,
            "sharpe_ratio": self.sharpe_ratio,
        }


@dataclass(frozen=True)
class ParameterSensitivityResult:
    """Strategy performance swept across neighbor values of one parameter."""

    param_name: str
    baseline_value: float
    points: list[ParameterSensitivityPoint]
    cliff_threshold: float

    @property
    def is_cliff(self) -> bool:
        """Whether performance is dominated by one abrupt jump between
        neighboring parameter values, rather than varying smoothly.

        Computed as the largest single adjacent-point change in
        `return_pct`, as a fraction of the total spread across all points.
        A value tuned to this exact dataset tends to show one sharp break
        right around it; a robust one varies gradually across the whole
        swept range.
        """
        returns = [
            p.return_pct
            for p in sorted(self.points, key=lambda p: p.param_value)
            if p.return_pct is not None
        ]
        if len(returns) < 2:
            return False

        spread = max(returns) - min(returns)
        if spread <= 0:
            return False

        largest_adjacent_jump = max(
            abs(returns[i + 1] - returns[i]) for i in range(len(returns) - 1)
        )
        return (largest_adjacent_jump / spread) > self.cliff_threshold

    def as_dict(self) -> dict:
        return {
            "param_name": self.param_name,
            "baseline_value": self.baseline_value,
            "is_cliff": self.is_cliff,
            "points": [p.as_dict() for p in self.points],
        }


def analyze_parameter_sensitivity(
    strategy_factory: Callable[[float], BaseStrategy],
    param_name: str,
    baseline_value: float,
    df: pd.DataFrame,
    perturbation_pcts: Sequence[float] = DEFAULT_PARAMETER_PERTURBATIONS,
    initial_capital: float = 1000.0,
    risk_per_trade_pct: float = 0.02,
    commission: float = 0.001,
    slippage_pct: float = 0.0005,
    cliff_threshold: float = 0.6,
) -> ParameterSensitivityResult:
    """Sweep one parameter around `baseline_value` and score each neighbor.

    Args:
        strategy_factory: Builds a strategy instance given one parameter
            value, e.g. `lambda v: DonchianBreakout(breakout_period=round(v))`.
            Decouples this engine from any specific strategy's constructor
            signature.
        param_name: Label only (surfaced in the result); not used to
            construct the strategy.
        baseline_value: The parameter's current/chosen value. Perturbations
            are relative to this.
        perturbation_pcts: Fractional offsets from `baseline_value`, e.g.
            `-0.2` for 20% below it. Defaults to the product's +/-20%
            neighbor-bounds sweep (see `DEFAULT_PARAMETER_PERTURBATIONS`).
        cliff_threshold: See `ParameterSensitivityResult.is_cliff`.

    Raises:
        ValueError: if `perturbation_pcts` is empty.
    """
    if not perturbation_pcts:
        raise ValueError("perturbation_pcts must be non-empty")

    points = []
    for pct in perturbation_pcts:
        value = baseline_value * (1 + pct)
        return_pct = None
        sharpe_ratio = None
        try:
            strategy = strategy_factory(value)
            risk_manager = RiskManager(
                account_equity=initial_capital, risk_per_trade_pct=risk_per_trade_pct
            )
            result = run_backtest(
                strategy,
                df,
                risk_manager,
                commission=commission,
                slippage_pct=slippage_pct,
            )
            equity = result.equity_curve["Equity"]
            if not equity.empty and float(equity.iloc[0]) > 0:
                return_pct = (float(equity.iloc[-1]) / float(equity.iloc[0]) - 1) * 100
                stats = compute_metrics(result.trades, equity_curve=equity)
                sharpe_ratio = stats.get("sharpe_ratio")
        except Exception:
            pass

        points.append(
            ParameterSensitivityPoint(
                param_value=value, return_pct=return_pct, sharpe_ratio=sharpe_ratio
            )
        )

    return ParameterSensitivityResult(
        param_name=param_name,
        baseline_value=baseline_value,
        points=points,
        cliff_threshold=cliff_threshold,
    )
