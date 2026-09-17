"""
Moving Average Crossover Strategy - reference implementation of BaseStrategy.

A minimal, well-understood strategy used as the template for new strategies:
BUY when the fast SMA crosses above the slow SMA, SELL when it crosses back
below. Stop-loss is a fixed percentage below/above entry; take-profit is a
multiple of ATR, demonstrating two of the three supported SL/TP types.
"""

import numpy as np
import pandas as pd

from .base_strategy import BaseStrategy


class MovingAverageCross(BaseStrategy):
    """Fast/slow SMA crossover with a PERCENTAGE stop-loss and ATR take-profit."""

    def __init__(
        self,
        fast_period: int = 20,
        slow_period: int = 50,
        atr_period: int = 14,
        sl_pct: float = 0.02,
        tp_atr_multiplier: float = 3.0,
    ):
        super().__init__(name="Moving Average Cross")

        if fast_period >= slow_period:
            raise ValueError("fast_period must be less than slow_period")

        self.fast_period = fast_period
        self.slow_period = slow_period
        self.atr_period = atr_period
        self.sl_pct = sl_pct
        self.tp_atr_multiplier = tp_atr_multiplier

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        df["sma_fast"] = df["Close"].rolling(window=self.fast_period).mean()
        df["sma_slow"] = df["Close"].rolling(window=self.slow_period).mean()

        tr = np.maximum(
            df["High"] - df["Low"],
            np.maximum(
                (df["High"] - df["Close"].shift()).abs(),
                (df["Low"] - df["Close"].shift()).abs(),
            ),
        )
        df["atr"] = tr.rolling(window=self.atr_period, min_periods=1).mean()

        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """See BaseStrategy.generate_signals for the output schema."""
        df = self._calculate_indicators(df)

        above = df["sma_fast"] > df["sma_slow"]
        golden_cross = above & ~above.shift(1, fill_value=False)
        death_cross = ~above & above.shift(1, fill_value=False)

        signal = pd.Series(0, index=df.index, dtype=int)
        signal[golden_cross] = 1
        signal[death_cross] = -1
        # No indicator warm-up yet -> no signal.
        signal[df["sma_slow"].isna()] = 0

        active = signal != 0

        sl_value = pd.Series(np.nan, index=df.index, dtype=float)
        tp_value = pd.Series(np.nan, index=df.index, dtype=float)
        sl_value[active] = self.sl_pct
        tp_value[active] = self.tp_atr_multiplier

        out = df.copy()
        out["signal"] = signal
        out["sl_type"] = np.where(active, "PERCENTAGE", None)
        out["sl_value"] = sl_value
        out["tp_type"] = np.where(active, "ATR", None)
        out["tp_value"] = tp_value

        return out

    def __repr__(self) -> str:
        return f"MovingAverageCross(fast={self.fast_period}, slow={self.slow_period})"
