"""
Volatility Contraction Pattern (VCP) Breakout Strategy.

Detects Mark Minervini-style bases: a sequence of consecutive price waves
each tighter (lower high-low range as a fraction of price) than the one
before it, with average volume declining wave over wave - the market
"drying up" before a move - followed by a breakout above the base's pivot
high on volume expansion.

All wave/base statistics are computed from bars strictly before the
breakout bar (every rolling window is `.shift()`ed past it), so nothing
here uses information not yet available at the time of the signal.
"""

import numpy as np
import pandas as pd

from ..indicators import weekly_ema, wilder_atr
from .base import BaseStrategy


class VCPBreakoutStrategy(BaseStrategy):
    """Contracting-wave base + volume dry-up + breakout expansion.

    Args:
        wave_period: Bars per contraction wave.
        num_contractions: Number of consecutive waves the base is split
            into (2-4 is the classic VCP range). The base itself spans
            `wave_period * num_contractions` bars.
        breakout_volume_multiple: Breakout-bar volume must exceed the most
            recent wave's average volume by this multiple.
        atr_period: Wilder ATR smoothing period, for the ATR-based stop/target.
        sl_atr_multiplier: Stop-loss distance, in ATR.
        tp_atr_multiplier: Take-profit distance, in ATR. Defaults keep the
            reward:risk ratio at 4.0 / 1.5 ≈ 2.67, above the risk engine's
            2.5R minimum by construction.
        weekly_ema_period: Weekly EMA span for the multi-timeframe
            confluence check - a breakout only counts while price also
            trades above its own weekly trend.
    """

    def __init__(
        self,
        wave_period: int = 15,
        num_contractions: int = 3,
        breakout_volume_multiple: float = 1.5,
        atr_period: int = 14,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 4.0,
        weekly_ema_period: int = 20,
    ):
        super().__init__(name="VCP Breakout")

        if wave_period < 2:
            raise ValueError("wave_period must be at least 2")
        if num_contractions < 2:
            raise ValueError(
                "num_contractions must be at least 2 (a VCP needs at least 2 waves)"
            )
        if breakout_volume_multiple <= 0:
            raise ValueError("breakout_volume_multiple must be positive")

        self.wave_period = wave_period
        self.num_contractions = num_contractions
        self.breakout_volume_multiple = breakout_volume_multiple
        self.atr_period = atr_period
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier
        self.weekly_ema_period = weekly_ema_period
        self.base_period = wave_period * num_contractions

    def _wave_metrics(self, df: pd.DataFrame) -> list[tuple[pd.Series, pd.Series]]:
        """Per-wave `(range_pct, avg_volume)`, oldest wave first.

        Wave `k`'s window is the `wave_period`-bar block ending
        `(num_contractions - 1 - k) * wave_period` bars before bar `t`,
        shifted so the *most recent* wave (`k == num_contractions - 1`)
        ends at `t - 1` - the breakout bar itself is never part of its own
        base.
        """
        high, low, close, volume = df["High"], df["Low"], df["Close"], df["Volume"]

        roll_range_pct = (
            high.rolling(self.wave_period).max() - low.rolling(self.wave_period).min()
        ) / close.rolling(self.wave_period).mean()
        roll_avg_volume = volume.rolling(self.wave_period).mean()

        waves = []
        for k in range(self.num_contractions):
            lag = 1 + (self.num_contractions - 1 - k) * self.wave_period
            waves.append((roll_range_pct.shift(lag), roll_avg_volume.shift(lag)))
        return waves

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["atr"] = wilder_atr(df, self.atr_period)
        df["weekly_ema"] = weekly_ema(df, span=self.weekly_ema_period)
        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """See BaseStrategy.generate_signals for the output schema."""
        df = self._calculate_indicators(df)
        waves = self._wave_metrics(df)

        contracting = pd.Series(True, index=df.index)
        volume_declining = pd.Series(True, index=df.index)
        for (prev_range, prev_volume), (curr_range, curr_volume) in zip(
            waves, waves[1:]
        ):
            contracting &= curr_range < prev_range
            volume_declining &= curr_volume <= prev_volume

        base_high = df["High"].rolling(self.base_period).max().shift(1)
        last_wave_volume = waves[-1][1]

        breakout = df["Close"] > base_high
        volume_expansion = (
            df["Volume"] > last_wave_volume * self.breakout_volume_multiple
        )
        weekly_confluence = df["Close"] > df["weekly_ema"]

        warm_up = pd.Series(True, index=df.index)
        warm_up.iloc[: self.base_period + 1] = False

        has_data = (
            base_high.notna()
            & last_wave_volume.notna()
            & df["atr"].notna()
            & df["weekly_ema"].notna()
        )

        buy = (
            warm_up
            & has_data
            & contracting
            & volume_declining
            & breakout
            & volume_expansion
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
        return (
            f"VCPBreakoutStrategy(wave_period={self.wave_period}, "
            f"num_contractions={self.num_contractions})"
        )
