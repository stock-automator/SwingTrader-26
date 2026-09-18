"""
Bollinger Band Squeeze + Keltner Channel Breakout Strategy.

The "TTM Squeeze": Bollinger Bands (a standard-deviation band around a
moving average) contract inside the Keltner Channel (an ATR band around an
EMA) when volatility compresses - the market coiling before a move. A
squeeze is "on" while the Bollinger Bands sit entirely inside the Keltner
Channel; it fires when the Bollinger Bands push back outside the Keltner
Channel on a close beyond the upper band, i.e. volatility expanding in the
direction of the breakout.

Every rolling/EWM window here is computed strictly from bars up to and
including `t` using only backward-looking pandas ops (`.rolling`,
`.ewm`), so nothing about the breakout bar itself needs `.shift()`ing -
the squeeze-held check below is what enforces "before today", not the band
math.
"""

import numpy as np
import pandas as pd

from ..indicators import wilder_atr
from .base import BaseStrategy


class BollingerKeltnerSqueezeStrategy(BaseStrategy):
    """Volatility-contraction squeeze + directional breakout release.

    Args:
        bb_period: Bollinger Band SMA/stdev window.
        bb_stddev: Bollinger Band width, in standard deviations.
        kc_period: Keltner Channel EMA and ATR window.
        kc_atr_multiplier: Keltner Channel width, in ATR multiples.
        min_squeeze_bars: Consecutive prior bars the squeeze must have held
            for a release to count as a genuine contraction-then-expansion
            rather than one noisy bar of band overlap.
        atr_period: Wilder ATR smoothing period, for the ATR-based stop/target.
        sl_atr_multiplier: Stop-loss distance, in ATR.
        tp_atr_multiplier: Take-profit distance, in ATR. Defaults keep the
            reward:risk ratio at 4.0 / 1.5 ≈ 2.67, above the risk engine's
            2.5R minimum by construction.
    """

    def __init__(
        self,
        bb_period: int = 20,
        bb_stddev: float = 2.0,
        kc_period: int = 20,
        kc_atr_multiplier: float = 1.5,
        min_squeeze_bars: int = 6,
        atr_period: int = 14,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 4.0,
    ):
        super().__init__(name="Bollinger-Keltner Squeeze Breakout")

        if bb_period < 2:
            raise ValueError("bb_period must be at least 2")
        if bb_stddev <= 0:
            raise ValueError("bb_stddev must be positive")
        if kc_period < 2:
            raise ValueError("kc_period must be at least 2")
        if kc_atr_multiplier <= 0:
            raise ValueError("kc_atr_multiplier must be positive")
        if min_squeeze_bars < 1:
            raise ValueError("min_squeeze_bars must be at least 1")

        self.bb_period = bb_period
        self.bb_stddev = bb_stddev
        self.kc_period = kc_period
        self.kc_atr_multiplier = kc_atr_multiplier
        self.min_squeeze_bars = min_squeeze_bars
        self.atr_period = atr_period
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        close = df["Close"]

        bb_mid = close.rolling(self.bb_period).mean()
        bb_std = close.rolling(self.bb_period).std()
        df["bb_upper"] = bb_mid + self.bb_stddev * bb_std
        df["bb_lower"] = bb_mid - self.bb_stddev * bb_std

        kc_mid = close.ewm(span=self.kc_period, adjust=False).mean()
        df["atr"] = wilder_atr(df, self.atr_period)
        kc_atr = wilder_atr(df, self.kc_period)
        df["kc_upper"] = kc_mid + self.kc_atr_multiplier * kc_atr
        df["kc_lower"] = kc_mid - self.kc_atr_multiplier * kc_atr

        df["squeeze_on"] = (df["bb_lower"] > df["kc_lower"]) & (
            df["bb_upper"] < df["kc_upper"]
        )
        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """See BaseStrategy.generate_signals for the output schema."""
        df = self._calculate_indicators(df)

        # The squeeze must have held for every one of the `min_squeeze_bars`
        # bars immediately before today - not today itself, which is the
        # release/breakout bar.
        squeeze_held = (
            df["squeeze_on"].shift(1).rolling(self.min_squeeze_bars).sum()
            == self.min_squeeze_bars
        )
        released = ~df["squeeze_on"]
        breakout = df["Close"] > df["bb_upper"]

        warm_up = pd.Series(True, index=df.index)
        warm_up.iloc[: max(self.bb_period, self.kc_period) + self.min_squeeze_bars] = (
            False
        )

        has_data = (
            df["bb_upper"].notna()
            & df["kc_upper"].notna()
            & df["atr"].notna()
            & squeeze_held.notna()
        )

        buy = warm_up & has_data & squeeze_held & released & breakout

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
            f"BollingerKeltnerSqueezeStrategy(bb_period={self.bb_period}, "
            f"kc_period={self.kc_period})"
        )
