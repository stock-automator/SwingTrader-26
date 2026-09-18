"""
Relative Strength Pullback Strategy.

Trades pullbacks in names already outperforming the benchmark: RS = the
ticker's trailing return minus the benchmark's trailing return over the
same rolling window (the same factor `quant/screener.py`'s
`RelativeStrengthScreener` ranks a universe by). A leader that dips to its
own short-term EMA and then resumes - while still outperforming the
benchmark and trading above its own weekly trend - is the entry.

Needs a benchmark price series the plain `BaseStrategy.generate_signals(df)`
signature has no room for, so it is supplied out of band via
`set_benchmark()` before the first `generate_signals()` call rather than
through the constructor - that keeps `strategies.build_strategy`'s generic,
JSON-params-only instantiation working for every registered strategy alike;
callers that have a benchmark frame loaded (the API layer, when running
this specific strategy) wire it in afterward.
"""

import numpy as np
import pandas as pd

from ..indicators import weekly_ema, wilder_atr
from .base import BaseStrategy


class RelativeStrengthStrategy(BaseStrategy):
    """RS-vs-benchmark leadership + short-EMA pullback + resumption.

    Args:
        rs_lookback: Rolling window (bars) the RS factor is measured over.
        trend_sma_period: Primary trend filter - price must stay above this
            SMA throughout the pullback for it to count as a pullback
            rather than a trend break.
        pullback_ema_period: Fast EMA a pullback dips to/through.
        pullback_lookback: How many bars back a dip to `pullback_ema_period`
            still counts as "the recent pullback" for today's resumption.
        atr_period: Wilder ATR smoothing period, for the ATR-based stop/target.
        sl_atr_multiplier: Stop-loss distance, in ATR.
        tp_atr_multiplier: Take-profit distance, in ATR. Defaults keep the
            reward:risk ratio at 4.0 / 1.5 ≈ 2.67, above the risk engine's
            2.5R minimum by construction.
        weekly_ema_period: Weekly EMA span for the multi-timeframe
            confluence check.
    """

    def __init__(
        self,
        rs_lookback: int = 63,
        trend_sma_period: int = 50,
        pullback_ema_period: int = 10,
        pullback_lookback: int = 5,
        atr_period: int = 14,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 4.0,
        weekly_ema_period: int = 20,
    ):
        super().__init__(name="Relative Strength Pullback")

        if rs_lookback < 2:
            raise ValueError("rs_lookback must be at least 2")
        if trend_sma_period < 2:
            raise ValueError("trend_sma_period must be at least 2")
        if pullback_ema_period < 2:
            raise ValueError("pullback_ema_period must be at least 2")
        if pullback_lookback < 1:
            raise ValueError("pullback_lookback must be at least 1")

        self.rs_lookback = rs_lookback
        self.trend_sma_period = trend_sma_period
        self.pullback_ema_period = pullback_ema_period
        self.pullback_lookback = pullback_lookback
        self.atr_period = atr_period
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier
        self.weekly_ema_period = weekly_ema_period
        self.benchmark: pd.DataFrame | None = None

    def set_benchmark(self, benchmark_df: pd.DataFrame) -> "RelativeStrengthStrategy":
        """Wire in the benchmark (e.g. SPY) OHLCV series RS is measured
        against. Must be called before `generate_signals`."""
        if "Close" not in benchmark_df.columns:
            raise ValueError("benchmark_df is missing required column: 'Close'")
        if benchmark_df.empty:
            raise ValueError("benchmark_df is empty")
        self.benchmark = benchmark_df
        return self

    def _relative_strength(self, df: pd.DataFrame) -> pd.Series:
        asset_return = df["Close"].pct_change(periods=self.rs_lookback)
        benchmark_close = (
            self.benchmark["Close"]
            .reindex(df.index.union(self.benchmark.index))
            .ffill()
            .reindex(df.index)
        )
        benchmark_return = benchmark_close.pct_change(periods=self.rs_lookback)
        return asset_return - benchmark_return

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["atr"] = wilder_atr(df, self.atr_period)
        df["weekly_ema"] = weekly_ema(df, span=self.weekly_ema_period)
        df["sma_trend"] = df["Close"].rolling(self.trend_sma_period).mean()
        df["ema_pullback"] = (
            df["Close"].ewm(span=self.pullback_ema_period, adjust=False).mean()
        )
        df["relative_strength"] = self._relative_strength(df)
        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """See BaseStrategy.generate_signals for the output schema.

        Raises:
            ValueError: if `set_benchmark` was not called first.
        """
        if self.benchmark is None:
            raise ValueError(
                "RelativeStrengthStrategy requires set_benchmark(...) "
                "before generate_signals"
            )

        df = self._calculate_indicators(df)

        trend_intact = df["Close"] > df["sma_trend"]
        rs_outperforming = df["relative_strength"] > 0

        touched_pullback = df["Low"] <= df["ema_pullback"]
        recently_touched = (
            touched_pullback.shift(1)
            .rolling(self.pullback_lookback, min_periods=1)
            .max()
            .astype(bool)
        )
        resumption = df["Close"] > df["High"].shift(1)
        weekly_confluence = df["Close"] > df["weekly_ema"]

        warm_up = pd.Series(True, index=df.index)
        min_history = (
            max(self.trend_sma_period, self.rs_lookback) + self.pullback_lookback
        )
        warm_up.iloc[:min_history] = False

        has_data = (
            df["sma_trend"].notna()
            & df["relative_strength"].notna()
            & df["atr"].notna()
            & df["weekly_ema"].notna()
        )

        buy = (
            warm_up
            & has_data
            & trend_intact
            & rs_outperforming
            & recently_touched
            & resumption
            & weekly_confluence
        )

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
        return f"RelativeStrengthStrategy(rs_lookback={self.rs_lookback})"
