"""
Monte Carlo trade-sequence bootstrap.

Resamples a strategy's own realised trade P&Ls - with replacement (a
classic i.i.d. bootstrap: the same trade can recur any number of times in
one simulated path) or without (a random shuffle of the same trades,
isolating sequence risk from distribution risk) - into thousands of
alternate equity paths a strategy with this trade distribution could
plausibly have produced. A single historical backtest is one draw from
that distribution; this answers "how much of that result was luck of the
draw" - Risk of Ruin, and how bad the worst few percent of paths'
drawdowns get - that the single curve alone cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

#: The product's recommended simulation count ("1,000+ trade-sequence
#: bootstrap randomizations") - the default every caller gets unless they
#: override it. Not a hard floor: a smaller value is still accepted (e.g.
#: for a fast smoke test), just not what production analysis should use.
DEFAULT_N_SIMULATIONS = 1000

#: Ruin = equity falls to or below this fraction of initial capital at any
#: point along a simulated path, e.g. 0.5 for a 50% drawdown. `0.0` would
#: mean only a total wipeout counts.
DEFAULT_RUIN_THRESHOLD_PCT = 0.5

#: Percentile bands returned for the equity-curve fan chart.
EQUITY_CURVE_PERCENTILES = (5, 25, 50, 75, 95)


@dataclass(frozen=True)
class MonteCarloResult:
    """Summary of one `run_monte_carlo` call."""

    n_simulations: int
    n_trades: int
    initial_capital: float
    with_replacement: bool
    ruin_threshold_pct: float
    risk_of_ruin_pct: float
    max_drawdown_p95_pct: float
    max_drawdown_p99_pct: float
    mean_final_equity: float
    median_final_equity: float
    final_equity_p05: float
    final_equity_p95: float
    equity_curve_percentiles: dict[str, list[float]]

    def as_dict(self) -> dict:
        return {
            "n_simulations": self.n_simulations,
            "n_trades": self.n_trades,
            "initial_capital": round(self.initial_capital, 2),
            "with_replacement": self.with_replacement,
            "ruin_threshold_pct": round(self.ruin_threshold_pct, 2),
            "risk_of_ruin_pct": round(self.risk_of_ruin_pct, 2),
            "max_drawdown_p95_pct": round(self.max_drawdown_p95_pct, 2),
            "max_drawdown_p99_pct": round(self.max_drawdown_p99_pct, 2),
            "mean_final_equity": round(self.mean_final_equity, 2),
            "median_final_equity": round(self.median_final_equity, 2),
            "final_equity_p05": round(self.final_equity_p05, 2),
            "final_equity_p95": round(self.final_equity_p95, 2),
            "equity_curve_percentiles": self.equity_curve_percentiles,
        }


def _simulate_equity_curves(
    trade_pnls: np.ndarray,
    initial_capital: float,
    n_simulations: int,
    with_replacement: bool,
    rng: np.random.Generator,
) -> np.ndarray:
    """`(n_simulations, n_trades + 1)` array of equity paths, column 0
    being `initial_capital` for every path."""
    n_trades = len(trade_pnls)

    if with_replacement:
        indices = rng.integers(0, n_trades, size=(n_simulations, n_trades))
    else:
        indices = np.array([rng.permutation(n_trades) for _ in range(n_simulations)])

    sequences = trade_pnls[indices]
    equity_after_each_trade = initial_capital + np.cumsum(sequences, axis=1)
    starting_column = np.full((n_simulations, 1), initial_capital)
    return np.concatenate([starting_column, equity_after_each_trade], axis=1)


def run_monte_carlo(
    trade_pnls: Sequence[float],
    initial_capital: float = 1000.0,
    n_simulations: int = DEFAULT_N_SIMULATIONS,
    with_replacement: bool = True,
    ruin_threshold_pct: float = DEFAULT_RUIN_THRESHOLD_PCT,
    seed: int | None = None,
) -> MonteCarloResult:
    """Bootstrap `trade_pnls` into `n_simulations` alternate equity paths.

    Args:
        trade_pnls: Each closed trade's absolute dollar P&L, in the order
            they actually closed. Order only matters when
            `with_replacement` is `False` (a shuffle needs something to
            shuffle); an i.i.d. bootstrap is order-independent by
            construction.
        initial_capital: Starting equity every simulated path begins at.
        n_simulations: Number of alternate paths to simulate. See
            `DEFAULT_N_SIMULATIONS`.
        with_replacement: `True` for a classic bootstrap (a trade may recur
            any number of times in one path); `False` for a
            without-replacement shuffle (each trade appears exactly once
            per path, in a random order).
        ruin_threshold_pct: Fraction of `initial_capital` that counts as
            "ruined" if equity ever falls to or below it along a path.
        seed: Deterministic seed for reproducible simulations (e.g. tests).

    Raises:
        ValueError: if `trade_pnls` is empty, `n_simulations` is not
            positive, `initial_capital` is non-positive, or
            `ruin_threshold_pct` is outside `[0, 1)`.
    """
    if len(trade_pnls) == 0:
        raise ValueError("trade_pnls must be non-empty")
    if n_simulations < 1:
        raise ValueError("n_simulations must be positive")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    if not 0 <= ruin_threshold_pct < 1:
        raise ValueError("ruin_threshold_pct must be in [0, 1)")

    pnls = np.asarray(trade_pnls, dtype=float)
    rng = np.random.default_rng(seed)
    equity = _simulate_equity_curves(
        pnls, initial_capital, n_simulations, with_replacement, rng
    )

    ruin_level = initial_capital * ruin_threshold_pct
    ruined = (equity <= ruin_level).any(axis=1)
    risk_of_ruin_pct = float(ruined.mean() * 100)

    running_peak = np.maximum.accumulate(equity, axis=1)
    drawdown_pct = (equity - running_peak) / running_peak * 100  # <= 0 everywhere
    drawdown_severity = -drawdown_pct.min(
        axis=1
    )  # worst drawdown per path, as a positive magnitude

    final_equity = equity[:, -1]

    equity_curve_percentiles = {
        f"p{p:02d}": np.round(np.percentile(equity, p, axis=0), 2).tolist()
        for p in EQUITY_CURVE_PERCENTILES
    }

    return MonteCarloResult(
        n_simulations=n_simulations,
        n_trades=len(pnls),
        initial_capital=initial_capital,
        with_replacement=with_replacement,
        ruin_threshold_pct=ruin_threshold_pct * 100,
        risk_of_ruin_pct=risk_of_ruin_pct,
        max_drawdown_p95_pct=float(np.percentile(drawdown_severity, 95)),
        max_drawdown_p99_pct=float(np.percentile(drawdown_severity, 99)),
        mean_final_equity=float(np.mean(final_equity)),
        median_final_equity=float(np.median(final_equity)),
        final_equity_p05=float(np.percentile(final_equity, 5)),
        final_equity_p95=float(np.percentile(final_equity, 95)),
        equity_curve_percentiles=equity_curve_percentiles,
    )
