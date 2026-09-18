"""
On-Balance Volume (OBV) Bullish Divergence Strategy.

OBV is a running total of volume, added on up days and subtracted on down
days - a proxy for whether volume is flowing into or out of a name
independent of price. A bullish divergence - price printing a lower low
while OBV prints a higher low over the same window - means selling pressure
is drying up even as price grinds down, which often precedes a reversal.
Confirmation waits for the actual turn (today's close above yesterday's
high) rather than trading the divergence the moment it's detected, since a
divergence with no turn yet is just as often the start of a fresh leg down.
"""

import numpy as np
import pandas as pd

from ..indicators import wilder_atr
from .base import BaseStrategy


def on_balance_volume(df: pd.DataFrame) -> pd.Series:
    """Cumulative volume signed by the direction of each day's close change.

    The first bar contributes 0 (no prior close to compare against).

    Raises:
        ValueError: if `df` is missing `Close`/`Volume` or is empty.
    """
    if df.empty:
        raise ValueError("df is empty")
    missing = [c for c in ("Close", "Volume") if c not in df.columns]
    if missing:
        raise ValueError(f"df is missing required column(s): {missing}")

    direction = np.sign(df["Close"].diff()).fillna(0.0)
    return (direction * df["Volume"]).cumsum().rename("obv")


class OBVDivergenceStrategy(BaseStrategy):
    """Bullish price/OBV divergence + turn confirmation.

    Args:
        divergence_lookback: Bars back a lower price low / higher OBV low is
            compared against.
        low_lookback: Window a "new low" is measured against, so the
            divergence check compares two genuine local lows rather than two
            arbitrary points.
        atr_period: Wilder ATR smoothing period, for the ATR-based stop/target.
        sl_atr_multiplier: Stop-loss distance, in ATR.
        tp_atr_multiplier: Take-profit distance, in ATR.
    """

    def __init__(
        self,
        divergence_lookback: int = 20,
        low_lookback: int = 5,
        atr_period: int = 14,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 3.5,
    ):
        super().__init__(name="OBV Bullish Divergence")

        if divergence_lookback < 2:
            raise ValueError("divergence_lookback must be at least 2")
        if low_lookback < 1:
            raise ValueError("low_lookback must be at least 1")

        self.divergence_lookback = divergence_lookback
        self.low_lookback = low_lookback
        self.atr_period = atr_period
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["obv"] = on_balance_volume(df)
        df["atr"] = wilder_atr(df, self.atr_period)
        df["rolling_low"] = df["Low"].rolling(self.low_lookback).min()
        df["obv_rolling_low"] = df["obv"].rolling(self.low_lookback).min()
        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """See BaseStrategy.generate_signals for the output schema."""
        df = self._calculate_indicators(df)

        prior_price_low = df["rolling_low"].shift(self.divergence_lookback)
        prior_obv_low = df["obv_rolling_low"].shift(self.divergence_lookback)

        lower_price_low = df["rolling_low"] < prior_price_low
        higher_obv_low = df["obv_rolling_low"] > prior_obv_low
        bullish_divergence = lower_price_low & higher_obv_low

        turn_confirmed = df["Close"] > df["High"].shift(1)

        warm_up = pd.Series(True, index=df.index)
        warm_up.iloc[: self.divergence_lookback + self.low_lookback] = False

        has_data = prior_price_low.notna() & prior_obv_low.notna() & df["atr"].notna()

        buy = warm_up & has_data & bullish_divergence & turn_confirmed

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
        return f"OBVDivergenceStrategy(divergence_lookback={self.divergence_lookback})"
