"""
Regime and earnings-blackout gating, as `BaseStrategy` wrappers.

`RegimeGatedStrategy` and `EarningsGatedStrategy` each wrap an inner
strategy and zero out its long entries on bars that fail an extra filter,
without touching the inner strategy's own signal logic. Wrapping rather
than baking the filters into `run_backtest`/`run_comparison` themselves
keeps those two - and every existing strategy/test built against them -
unchanged: the Backtest Studio's "Regime Gating" and "Earnings Blackout"
toggles are implemented entirely at this layer, by the API route choosing
whether to wrap the requested strategy before running it.
"""

from __future__ import annotations

import pandas as pd

from .regime import MACRO_BLOCKED_REGIMES, MacroRegimeDetector
from .screener import CatalystFilter
from .strategies.base import BaseStrategy


def _reindex_ffill(series: pd.Series, target_index: pd.Index) -> pd.Series:
    """`series` forward-filled onto `target_index`, without ever reading a
    value from a date later than the one being filled in - the same
    look-ahead-safe reindex pattern used by `quant/backtest.py`'s
    `align_curves`."""
    return (
        series.reindex(series.index.union(target_index)).ffill().reindex(target_index)
    )


class RegimeGatedStrategy(BaseStrategy):
    """Suppresses an inner strategy's long entries while the macro (SPY)
    regime is in `blocked_regimes`.

    Args:
        inner: The wrapped strategy - its own signal logic is untouched;
            only bars where it emits a long entry can be overridden to flat.
        benchmark_df: OHLCV bars for the regime benchmark (SPY), passed to
            `detector`.
        detector: Defaults to a `MacroRegimeDetector` with its own defaults.
        blocked_regimes: Macro regimes that suppress new longs. Defaults to
            `MACRO_BLOCKED_REGIMES` (`BEAR_TRENDING`, `HIGH_VOLATILITY_CHOP`).
    """

    def __init__(
        self,
        inner: BaseStrategy,
        benchmark_df: pd.DataFrame,
        detector: MacroRegimeDetector | None = None,
        blocked_regimes: frozenset[str] = MACRO_BLOCKED_REGIMES,
    ):
        super().__init__(name=f"{inner.name} (regime-gated)")
        self.inner = inner
        self.benchmark_df = benchmark_df
        self.detector = detector or MacroRegimeDetector()
        self.blocked_regimes = frozenset(blocked_regimes)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = self.inner.generate_signals(df)

        regime = self.detector.detect_regime(self.benchmark_df)
        regime_on_df = _reindex_ffill(regime, df.index)
        blocked = regime_on_df.isin(self.blocked_regimes)

        out = signals.copy()
        out.loc[blocked & (out["signal"] == 1), "signal"] = 0
        return out


class EarningsGatedStrategy(BaseStrategy):
    """Suppresses an inner strategy's long entries within a trading-day
    blackout window of a known earnings date.

    Args:
        inner: The wrapped strategy.
        earnings_dates: Earnings report dates applied to every ticker this
            strategy is run over. The simple, single-ticker case - an empty
            list is a no-op, the inner strategy's signals pass through
            unchanged.
        earnings_by_ticker: Per-ticker earnings dates, keyed by
            `df.attrs["ticker"]` on the frame passed to `generate_signals`.
            Lets one strategy instance be reused across a multi-ticker
            backtest's sleeves (`quant/backtest.py`'s `run_comparison` shares
            a single strategy object across every ticker's frame) while
            still blocking each sleeve on *its own* earnings calendar. When
            a frame's tagged ticker has an entry here, it takes precedence
            over `earnings_dates` for that call; an untagged frame (or one
            missing from this map) falls back to `earnings_dates`.
        catalyst_filter: Defaults to a `CatalystFilter` with its own default
            blackout window.
    """

    def __init__(
        self,
        inner: BaseStrategy,
        earnings_dates: list[pd.Timestamp] | None = None,
        earnings_by_ticker: dict[str, list[pd.Timestamp]] | None = None,
        catalyst_filter: CatalystFilter | None = None,
    ):
        super().__init__(name=f"{inner.name} (earnings-gated)")
        self.inner = inner
        self.earnings_dates = list(earnings_dates or [])
        self.earnings_by_ticker = earnings_by_ticker
        self.catalyst_filter = catalyst_filter or CatalystFilter()

    def _dates_for(self, df: pd.DataFrame) -> list[pd.Timestamp]:
        ticker = df.attrs.get("ticker")
        if self.earnings_by_ticker is not None and ticker in self.earnings_by_ticker:
            return self.earnings_by_ticker[ticker]
        return self.earnings_dates

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = self.inner.generate_signals(df)
        dates = self._dates_for(df)
        if not dates:
            return signals

        out = signals.copy()
        buy_dates = out.index[out["signal"] == 1]
        blocked_dates = [
            ts for ts in buy_dates if self.catalyst_filter.is_blocked(ts, dates)
        ]
        if blocked_dates:
            out.loc[blocked_dates, "signal"] = 0
        return out
