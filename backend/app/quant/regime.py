"""
Trend-strength market regime detection.

Classifies each bar into `BULL_TREND`, `BEAR_TREND`, or `CHOPPY` using
Wilder's ADX/+DI/-DI (trend strength and direction) alongside a
Wilder-smoothed ATR (volatility magnitude, exposed for callers that want
to size off it).

Consumed by the live screener (`api/screener.py`), which uses the regime to
label each setup long or short, and exposes `atr` in the form
`RiskManager.volatility_parity_size` takes directly.
"""

import numpy as np
import pandas as pd

from .indicators import true_range, wilder_atr

REGIME_BULL_TREND = "BULL_TREND"
REGIME_BEAR_TREND = "BEAR_TREND"
REGIME_CHOPPY = "CHOPPY"
REGIME_UNKNOWN = "UNKNOWN"


class RegimeDetector:
    """Classifies market regime from OHLC price action via ADX/+DI/-DI.

    Args:
        adx_period: Wilder smoothing period for +DM/-DM/ADX.
        atr_period: Wilder smoothing period for the ATR column added
            alongside the regime indicators (not used in the regime decision
            itself, exposed for callers that want ATR-based sizing/filtering).
        adx_trend_threshold: Minimum ADX to call a bar trending; below this
            the bar is classified `CHOPPY` regardless of +DI/-DI.
    """

    def __init__(
        self,
        adx_period: int = 14,
        atr_period: int = 14,
        adx_trend_threshold: float = 25.0,
    ):
        if adx_period < 2:
            raise ValueError("adx_period must be at least 2")
        if atr_period < 2:
            raise ValueError("atr_period must be at least 2")
        if adx_trend_threshold <= 0:
            raise ValueError("adx_trend_threshold must be positive")

        self.adx_period = adx_period
        self.atr_period = atr_period
        self.adx_trend_threshold = adx_trend_threshold

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a copy of `df` with `plus_di`, `minus_di`, `adx`, `atr` added.

        Raises:
            ValueError: if `df` is empty or missing `High`/`Low`/`Close`.
                Checked explicitly so a malformed frame fails with the same
                exception type the rest of `src/core` uses, rather than a
                bare `KeyError` from deep inside the indicator math.
        """
        missing = [c for c in ("High", "Low", "Close") if c not in df.columns]
        if missing:
            raise ValueError(f"df is missing required column(s): {missing}")
        if df.empty:
            raise ValueError("df is empty")

        df = df.copy()

        high, low = df["High"], df["Low"]
        prev_high = high.shift(1)
        prev_low = low.shift(1)

        tr = true_range(df)

        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm = pd.Series(
            np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
            index=df.index,
        )
        minus_dm = pd.Series(
            np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
            index=df.index,
        )

        alpha = 1.0 / self.adx_period
        atr_for_di = tr.ewm(
            alpha=alpha, adjust=False, min_periods=self.adx_period
        ).mean()
        smoothed_plus_dm = plus_dm.ewm(
            alpha=alpha, adjust=False, min_periods=self.adx_period
        ).mean()
        smoothed_minus_dm = minus_dm.ewm(
            alpha=alpha, adjust=False, min_periods=self.adx_period
        ).mean()

        plus_di = 100 * (smoothed_plus_dm / atr_for_di)
        minus_di = 100 * (smoothed_minus_dm / atr_for_di)

        di_sum = plus_di + minus_di
        dx = 100 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan)
        adx = dx.ewm(alpha=alpha, adjust=False, min_periods=self.adx_period).mean()

        # Same Wilder definition the strategies price their ATR stops off and
        # the ADX path smooths its own true range with above, so an ATR
        # consumed downstream (e.g. RiskManager's volatility-parity sizing) is
        # the definition the regime decision is built on. With the default
        # atr_period == adx_period this is exactly `atr_for_di`.
        atr = wilder_atr(df, self.atr_period)

        df["plus_di"] = plus_di
        df["minus_di"] = minus_di
        df["adx"] = adx
        df["atr"] = atr
        return df

    def detect_regime(self, df: pd.DataFrame) -> pd.Series:
        """Per-bar regime classification, indexed like `df`.

        Bars still inside the indicator warm-up window are `None`.
        """
        indicators = self.compute_indicators(df)

        trending = indicators["adx"] >= self.adx_trend_threshold
        bullish = indicators["plus_di"] > indicators["minus_di"]

        regime = pd.Series(REGIME_CHOPPY, index=df.index, dtype=object)
        regime[trending & bullish] = REGIME_BULL_TREND
        regime[trending & ~bullish] = REGIME_BEAR_TREND
        regime[indicators["adx"].isna()] = None
        return regime

    def current_regime(self, df: pd.DataFrame) -> str:
        """Latest-bar regime, or `'UNKNOWN'` if there isn't enough history."""
        if len(df) < self.adx_period * 2:
            return REGIME_UNKNOWN

        regime = self.detect_regime(df)
        latest = regime.iloc[-1]
        return latest if latest is not None else REGIME_UNKNOWN
