"""
Real win-probability engine.

`api/signals.py`'s live setup grid used to ship every row with
`win_probability: null` and an explicit "no model exists yet" note - honest,
but not useful. This module replaces that placeholder with an actual
estimate, computed two ways depending on how much history backs it:

1. **Regime-matched historical backtest** - run the same strategy over the
   ticker's own full price history via `quant.engine.run_backtest`, tag each
   closed trade with the market regime (`quant.regime.RegimeDetector`) that
   was active when it was entered, and take the win rate of trades entered
   in the *same* regime as today's setup. This is the strongest estimate
   because it conditions on the thing that actually varies strategy
   performance the most (trending vs. choppy tape), not just "this
   strategy's win rate everywhere, ever."
2. **Block-bootstrap Monte Carlo percentile** - when there aren't enough
   regime-matched trades to trust a raw ratio, resample the *entire* trade
   history's win/loss sequence (in contiguous blocks, so streaks/serial
   correlation survive the resample rather than being scrambled away) many
   times and report the resampled distribution's median win rate plus a
   5th/95th percentile band. Weaker signal than (1) - it isn't conditioned
   on regime - but still real, and it comes with an honest confidence band
   instead of a single number pretending to be precise.

If neither has enough trades to say anything (a new ticker, an
illiquid/rarely-triggering strategy), `win_probability` stays `None` - the
same honesty the placeholder had, just now reserved for when it's actually
true rather than applied unconditionally.

Short setups are screening-only (`engine.run_backtest` is long-only - see
its module docstring), so this module never estimates a win probability for
`direction="SHORT"`; callers should not call `estimate_win_probability` for
those rows at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..quant.engine import BacktestResult, run_backtest
from ..quant.regime import RegimeDetector
from ..quant.risk import RiskManager
from ..quant.strategies.base import BaseStrategy

log = logging.getLogger(__name__)

#: Below this many regime-matched trades, a raw win/loss ratio is too noisy
#: to report as-is (e.g. 3/4 wins "75%" from four trades is not a real 75%
#: edge) - fall back to the whole-history bootstrap instead.
MIN_REGIME_MATCHED_TRADES = 12

#: Below this many *total* closed trades (regardless of regime), there isn't
#: enough history for even a bootstrap resample to mean anything - report
#: `None` rather than a number with no real support behind it.
MIN_BOOTSTRAP_TRADES = 8

#: Trade-sequence bootstrap iterations for the percentile fallback.
DEFAULT_BOOTSTRAP_ITERATIONS = 2000

#: Block size for the bootstrap resample: contiguous runs of this many
#: trades are resampled as a unit, preserving local win/loss streaks instead
#: of an i.i.d. shuffle that would erase them.
DEFAULT_BLOCK_SIZE = 4

METHOD_REGIME_MATCHED = "regime_matched_backtest"
METHOD_BLOCK_BOOTSTRAP = "block_bootstrap"
METHOD_INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class WinProbabilityEstimate:
    """Result of `estimate_win_probability` for one (strategy, ticker,
    regime) combination."""

    win_probability: float | None
    method: str
    sample_size: int
    confidence_low: float | None = None
    confidence_high: float | None = None
    note: str | None = None

    def as_dict(self) -> dict:
        return {
            "win_probability": (
                round(self.win_probability, 4)
                if self.win_probability is not None
                else None
            ),
            "win_probability_method": self.method,
            "win_probability_sample_size": self.sample_size,
            "win_probability_confidence_low": (
                round(self.confidence_low, 4)
                if self.confidence_low is not None
                else None
            ),
            "win_probability_confidence_high": (
                round(self.confidence_high, 4)
                if self.confidence_high is not None
                else None
            ),
            "win_probability_note": self.note,
        }


def _insufficient(sample_size: int, note: str) -> WinProbabilityEstimate:
    return WinProbabilityEstimate(
        win_probability=None,
        method=METHOD_INSUFFICIENT_DATA,
        sample_size=sample_size,
        note=note,
    )


def _trade_regimes(
    trades: pd.DataFrame, df: pd.DataFrame, regime_detector: RegimeDetector
) -> pd.Series:
    """Regime active at each trade's `EntryTime`, aligned to `trades.index`.

    Bars still inside the regime detector's indicator warm-up window (or an
    `EntryTime` that doesn't land exactly on a bar) map to `None`.
    """
    regimes = regime_detector.detect_regime(df)
    # `asof`-style alignment: an EntryTime should always land exactly on one
    # of df's bars (the backtest engine only ever fills on df's own bars),
    # but reindex-with-nearest keeps this from exploding if it doesn't.
    aligned = regimes.reindex(trades["EntryTime"], method=None)
    return pd.Series(aligned.to_numpy(), index=trades.index)


def _block_bootstrap_win_rates(
    outcomes: np.ndarray,
    n_iterations: int,
    block_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """`n_iterations` resampled win rates from a block bootstrap of
    `outcomes` (a 0/1 win indicator array, in the order trades closed)."""
    n = len(outcomes)
    block_size = max(1, min(block_size, n))
    n_blocks = -(-n // block_size)  # ceil division: enough blocks to cover n

    # Every valid block start index, so a block never runs off the end.
    max_start = n - block_size
    starts = rng.integers(0, max_start + 1, size=(n_iterations, n_blocks))

    win_rates = np.empty(n_iterations, dtype=float)
    offsets = np.arange(block_size)
    for i in range(n_iterations):
        idx = (starts[i][:, None] + offsets[None, :]).ravel()[:n]
        win_rates[i] = outcomes[idx].mean()
    return win_rates


def estimate_win_probability(
    strategy: BaseStrategy,
    df: pd.DataFrame,
    risk_manager: RiskManager,
    current_regime: str,
    regime_detector: RegimeDetector | None = None,
    n_bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    block_size: int = DEFAULT_BLOCK_SIZE,
    seed: int | None = None,
) -> WinProbabilityEstimate:
    """Historical, regime-aware win-probability estimate for a LONG setup.

    Args:
        strategy: The same strategy instance the live setup was generated
            from (already `set_benchmark`-wired if it needs one).
        df: The ticker's full available OHLCV history - not sliced to
            "today", so the backtest below has as many closed trades as
            actually exist to learn from.
        risk_manager: Used to size the historical backtest identically to
            how the live setup itself was sized.
        current_regime: The regime label (`quant.regime.REGIME_*`) the live
            setup was generated under - trades are matched against this.
        seed: Deterministic seed for the bootstrap fallback (tests).

    Returns:
        `WinProbabilityEstimate` with `win_probability=None` if there isn't
        enough trade history to support either method - never a fabricated
        number.
    """
    detector = regime_detector or RegimeDetector()

    try:
        result: BacktestResult = run_backtest(strategy, df, risk_manager)
    except Exception as exc:  # a single strategy's backtest must not 500 the grid
        log.warning("expectancy: backtest failed, no win probability: %s", exc)
        return _insufficient(0, f"Historical backtest failed: {exc}")

    trades = result.trades
    if trades.empty or "PnL" not in trades.columns:
        return _insufficient(0, "No closed historical trades for this ticker/strategy.")

    outcomes = (trades["PnL"] > 0).to_numpy(dtype=float)
    n_trades = len(outcomes)

    if "EntryTime" in trades.columns:
        trade_regimes = _trade_regimes(trades, df, detector)
        regime_mask = (trade_regimes == current_regime).to_numpy()
        regime_outcomes = outcomes[regime_mask]
    else:
        regime_outcomes = np.array([])

    if len(regime_outcomes) >= MIN_REGIME_MATCHED_TRADES:
        win_rate = float(regime_outcomes.mean())
        return WinProbabilityEstimate(
            win_probability=win_rate,
            method=METHOD_REGIME_MATCHED,
            sample_size=len(regime_outcomes),
            note=(
                f"{len(regime_outcomes)} historical trades entered in "
                f"{current_regime} regime."
            ),
        )

    if n_trades >= MIN_BOOTSTRAP_TRADES:
        rng = np.random.default_rng(seed)
        simulated = _block_bootstrap_win_rates(
            outcomes, n_bootstrap_iterations, block_size, rng
        )
        return WinProbabilityEstimate(
            win_probability=float(np.percentile(simulated, 50)),
            method=METHOD_BLOCK_BOOTSTRAP,
            sample_size=n_trades,
            confidence_low=float(np.percentile(simulated, 5)),
            confidence_high=float(np.percentile(simulated, 95)),
            note=(
                f"Only {len(regime_outcomes)} trades matched the current "
                f"{current_regime} regime (need {MIN_REGIME_MATCHED_TRADES}+); "
                f"falling back to a {n_bootstrap_iterations}-iteration block "
                f"bootstrap over all {n_trades} historical trades."
            ),
        )

    return _insufficient(
        n_trades,
        f"Only {n_trades} historical closed trade(s) for this ticker/strategy - "
        f"need at least {MIN_BOOTSTRAP_TRADES} for even a bootstrap estimate.",
    )
