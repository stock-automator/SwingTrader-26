"""
Dual Momentum Engine (Relative + Absolute Momentum).

Gary Antonacci's dual momentum: trade a name only when it clears *both*
momentum tests over the same lookback window - absolute momentum (its own
trailing return is positive, i.e. it would beat cash) and relative momentum
(that return also beats the benchmark's). Either test alone is weaker:
absolute-only will hold a name that's merely drifting up slower than the
market; relative-only will hold a leader inside a bear market that is still
losing money in absolute terms.

Needs a benchmark price series the plain `BaseStrategy.generate_signals(df)`
signature has no room for, so - like `RelativeStrengthStrategy` - it is
supplied out of band via `set_benchmark()` before the first
`generate_signals()` call rather than through the constructor, keeping
`strategies.build_strategy`'s generic, JSON-params-only instantiation
working for every registered strategy alike.
"""

import numpy as np
import pandas as pd

from ..indicators import wilder_atr
from .base import BaseStrategy


class DualMomentumStrategy(BaseStrategy):
    """Absolute momentum (own trailing return > 0) + relative momentum
    (beats the benchmark) + a trend filter against whipsawing near flat.

    Args:
        momentum_lookback: Rolling window (bars) both momentum legs are
            measured over.
        trend_sma_period: Price must stay above this SMA - dual momentum
            being positive at the exact moment a trend breaks down is a
            lagging read, not a fresh entry.
        atr_period: Wilder ATR smoothing period, for the ATR-based stop/target.
        sl_atr_multiplier: Stop-loss distance, in ATR.
        tp_atr_multiplier: Take-profit distance, in ATR.
    """

    def __init__(
        self,
        momentum_lookback: int = 126,
        trend_sma_period: int = 50,
        atr_period: int = 14,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 4.0,
    ):
        super().__init__(name="Dual Momentum Engine")

        if momentum_lookback < 2:
            raise ValueError("momentum_lookback must be at least 2")
        if trend_sma_period < 2:
            raise ValueError("trend_sma_period must be at least 2")

        self.momentum_lookback = momentum_lookback
        self.trend_sma_period = trend_sma_period
        self.atr_period = atr_period
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier
        self.benchmark: pd.DataFrame | None = None

    def set_benchmark(self, benchmark_df: pd.DataFrame) -> "DualMomentumStrategy":
        """Wire in the benchmark (e.g. SPY) OHLCV series absolute momentum
        is compared against. Must be called before `generate_signals`."""
        if "Close" not in benchmark_df.columns:
            raise ValueError("benchmark_df is missing required column: 'Close'")
        if benchmark_df.empty:
            raise ValueError("benchmark_df is empty")
        self.benchmark = benchmark_df
        return self

    def _benchmark_return(self, df: pd.DataFrame) -> pd.Series:
        benchmark_close = (
            self.benchmark["Close"]
            .reindex(df.index.union(self.benchmark.index))
            .ffill()
            .reindex(df.index)
        )
        return benchmark_close.pct_change(periods=self.momentum_lookback)

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["atr"] = wilder_atr(df, self.atr_period)
        df["sma_trend"] = df["Close"].rolling(self.trend_sma_period).mean()
        df["asset_momentum"] = df["Close"].pct_change(periods=self.momentum_lookback)
        df["benchmark_momentum"] = self._benchmark_return(df)
        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """See BaseStrategy.generate_signals for the output schema.

        Raises:
            ValueError: if `set_benchmark` was not called first.
        """
        if self.benchmark is None:
            raise ValueError(
                "DualMomentumStrategy requires set_benchmark(...) before generate_signals"
            )

        df = self._calculate_indicators(df)

        absolute_momentum = df["asset_momentum"] > 0
        relative_momentum = df["asset_momentum"] > df["benchmark_momentum"]
        trend_intact = df["Close"] > df["sma_trend"]

        warm_up = pd.Series(True, index=df.index)
        warm_up.iloc[: max(self.momentum_lookback, self.trend_sma_period)] = False

        has_data = (
            df["asset_momentum"].notna()
            & df["benchmark_momentum"].notna()
            & df["sma_trend"].notna()
            & df["atr"].notna()
        )

        buy = warm_up & has_data & absolute_momentum & relative_momentum & trend_intact

        signal = pd.Series(0, index=df.index, dtype=int)
        signal[buy] = 1

        sl_value = pd.Series(np.nan, index=df.index, dtype=float)
        tp_value = pd.Series(np.nan, index=df.index, dtype=float)
        sl_value[buy] = self.sl_atr_multiplier
        tp_value[buy] = self.tp_atr_multiplier

        out = df.copy()
        out["signal"] = signal
        out["sl_type"] = np.where(buy, "ATR", None)
        out["sl_value"] = sl_value
        out["tp_type"] = np.where(buy, "ATR", None)
        out["tp_value"] = tp_value

        return out

    def __repr__(self) -> str:
        return f"DualMomentumStrategy(momentum_lookback={self.momentum_lookback})"
