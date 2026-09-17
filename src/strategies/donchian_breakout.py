"""
Donchian 20-day Momentum Breakout Strategy
- 20-day breakout (against YESTERDAY's high, not today's)
- 50 EMA trend filter
- 3-month momentum
- Volume confirmation
- ATR-based stop/target
"""

import numpy as np
import pandas as pd

from .base_strategy import BaseStrategy


class DonchianBreakout(BaseStrategy):
    """Breakout + trend + momentum + volume confirmation."""

    def __init__(
        self,
        breakout_period: int = 20,
        ema_period: int = 50,
        momentum_period: int = 63,  # 3 months
        volume_period: int = 20,
        atr_period: int = 14,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 3.5,
    ):
        super().__init__(name="Donchian 20-day Breakout")

        self.breakout_period = breakout_period
        self.ema_period = ema_period
        self.momentum_period = momentum_period
        self.volume_period = volume_period
        self.atr_period = atr_period
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all indicators needed for the strategy."""
        df = df.copy()

        # ========== DONCHIAN BREAKOUT ==========
        df["donchian_high"] = df["High"].rolling(window=self.breakout_period).max()
        df["donchian_low"] = df["Low"].rolling(window=self.breakout_period).min()

        # ========== EMA TREND FILTER ==========
        df["ema_50"] = df["Close"].ewm(span=self.ema_period, adjust=False).mean()

        # ========== MOMENTUM ==========
        df["returns_3m"] = df["Close"].pct_change(periods=self.momentum_period)
        df["returns_3m"] = df["returns_3m"].fillna(0)
        df["momentum_positive"] = df["returns_3m"] > 0

        # ========== VOLUME CONFIRMATION ==========
        df["volume_ma"] = (
            df["Volume"].rolling(window=self.volume_period, min_periods=1).mean()
        )
        df["volume_ma"] = df["volume_ma"].fillna(df["Volume"].mean())
        df["volume_spike"] = df["Volume"] > df["volume_ma"]

        # ========== ATR FOR POSITION SIZING & STOPS ==========
        df["tr"] = np.maximum(
            df["High"] - df["Low"],
            np.maximum(
                (df["High"] - df["Close"].shift()).abs(),
                (df["Low"] - df["Close"].shift()).abs(),
            ),
        )
        df["atr"] = df["tr"].rolling(window=self.atr_period, min_periods=1).mean()
        df["atr"] = df["atr"].fillna(df["tr"].mean())

        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate BUY signals when breakout conditions are met.

        See BaseStrategy.generate_signals for the output schema.
        """
        df = self._calculate_indicators(df)

        prev_donchian_high = df["donchian_high"].shift(1)

        warm_up = pd.Series(True, index=df.index)
        warm_up.iloc[: max(self.breakout_period, self.momentum_period)] = False

        has_data = (
            df["Close"].notna()
            & prev_donchian_high.notna()
            & df["ema_50"].notna()
            & df["volume_ma"].notna()
            & df["atr"].notna()
        )

        donchian_break = df["Close"] > prev_donchian_high
        above_ema = df["Close"] > df["ema_50"]
        positive_momentum = df["momentum_positive"]
        volume_confirmed = df["Volume"] > df["volume_ma"]

        buy = (
            warm_up
            & has_data
            & donchian_break
            & above_ema
            & positive_momentum
            & volume_confirmed
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

    def __repr__(self):
        return (
            f"DonchianBreakout(breakout={self.breakout_period}, "
            f"ema={self.ema_period}, momentum={self.momentum_period})"
        )
