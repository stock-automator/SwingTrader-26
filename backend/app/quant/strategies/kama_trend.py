"""
Kaufman Adaptive Moving Average (KAMA) Dynamic Trend Strategy.

KAMA speeds up to track price closely in a trending, low-noise market and
slows down to filter out whipsaws in a choppy one, by scaling its smoothing
constant with an "efficiency ratio" - net directional movement over a
window divided by the sum of the bar-to-bar movement in that window. A
ratio near 1 means price moved in a straight line (fast tracking is safe);
near 0 means it churned sideways (slow tracking avoids chasing noise).

KAMA's recursive definition (`kama[t]` depends on `kama[t-1]`) is not
expressible as a single vectorized pandas op, so it's computed with an
explicit loop - the efficiency ratio and smoothing constant feeding it are
still fully vectorized.
"""

import numpy as np
import pandas as pd

from ..indicators import wilder_atr
from .base import BaseStrategy


def kaufman_efficiency_ratio(close: pd.Series, period: int) -> pd.Series:
    """Net change over `period` bars divided by the sum of absolute bar-to-bar
    changes over the same window - 1.0 for a straight-line move, near 0.0
    for pure noise. NaN over the warm-up window."""
    change = close.diff(period).abs()
    volatility = close.diff().abs().rolling(period).sum()
    # A flat window (volatility == 0) has no direction to be efficient
    # about; call it 0 rather than dividing by zero into inf/NaN.
    return (change / volatility.replace(0, np.nan)).fillna(0.0)


def kama(
    close: pd.Series,
    er_period: int = 10,
    fast_period: int = 2,
    slow_period: int = 30,
) -> pd.Series:
    """Kaufman Adaptive Moving Average, indexed like `close`.

    Args:
        er_period: Efficiency-ratio lookback window.
        fast_period: Fastest EMA-equivalent period, applied when the
            efficiency ratio is at its maximum (1.0).
        slow_period: Slowest EMA-equivalent period, applied when the
            efficiency ratio is at its minimum (0.0).

    Raises:
        ValueError: if `close` is empty, or any period is less than 1.
    """
    if close.empty:
        raise ValueError("close is empty")
    if er_period < 1 or fast_period < 1 or slow_period < 1:
        raise ValueError("er_period, fast_period, slow_period must all be at least 1")

    er = kaufman_efficiency_ratio(close, er_period)
    fast_sc = 2.0 / (fast_period + 1)
    slow_sc = 2.0 / (slow_period + 1)
    smoothing_constant = (er * (fast_sc - slow_sc) + slow_sc) ** 2

    values = close.to_numpy(dtype=float)
    sc = smoothing_constant.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)

    warm_up = er_period
    if len(values) <= warm_up:
        return pd.Series(out, index=close.index, name="kama")

    out[warm_up] = values[warm_up]
    for t in range(warm_up + 1, len(values)):
        prev = out[t - 1]
        step = sc[t] if np.isfinite(sc[t]) else slow_sc
        out[t] = prev + step * (values[t] - prev)

    return pd.Series(out, index=close.index, name="kama")


class KAMATrendStrategy(BaseStrategy):
    """KAMA trend-following: price crossing above a rising adaptive average.

    Args:
        er_period: Efficiency-ratio lookback window KAMA adapts its
            smoothing constant from.
        fast_period: Fastest EMA-equivalent period (trending regime).
        slow_period: Slowest EMA-equivalent period (choppy regime).
        rising_lookback: Bars over which KAMA must have net risen for the
            trend filter to confirm.
        atr_period: Wilder ATR smoothing period, for the ATR-based stop/target.
        sl_atr_multiplier: Stop-loss distance, in ATR.
        tp_atr_multiplier: Take-profit distance, in ATR.
    """

    def __init__(
        self,
        er_period: int = 10,
        fast_period: int = 2,
        slow_period: int = 30,
        rising_lookback: int = 5,
        atr_period: int = 14,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 3.5,
    ):
        super().__init__(name="KAMA Dynamic Trend")

        if er_period < 1:
            raise ValueError("er_period must be at least 1")
        if fast_period < 1:
            raise ValueError("fast_period must be at least 1")
        if slow_period <= fast_period:
            raise ValueError("slow_period must be greater than fast_period")
        if rising_lookback < 1:
            raise ValueError("rising_lookback must be at least 1")

        self.er_period = er_period
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.rising_lookback = rising_lookback
        self.atr_period = atr_period
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["kama"] = kama(
            df["Close"], self.er_period, self.fast_period, self.slow_period
        )
        df["atr"] = wilder_atr(df, self.atr_period)
        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """See BaseStrategy.generate_signals for the output schema."""
        df = self._calculate_indicators(df)

        kama_rising = df["kama"] > df["kama"].shift(self.rising_lookback)
        cross_above = (df["Close"] > df["kama"]) & (
            df["Close"].shift(1) <= df["kama"].shift(1)
        )

        warm_up = pd.Series(True, index=df.index)
        warm_up.iloc[: self.er_period + self.rising_lookback + 1] = False

        has_data = (
            df["kama"].notna()
            & df["kama"].shift(self.rising_lookback).notna()
            & df["atr"].notna()
        )

        buy = warm_up & has_data & cross_above & kama_rising

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
            f"KAMATrendStrategy(er_period={self.er_period}, "
            f"fast={self.fast_period}, slow={self.slow_period})"
        )
