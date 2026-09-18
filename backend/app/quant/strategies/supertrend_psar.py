"""
Supertrend + Parabolic SAR Confluence Strategy.

Both indicators are trend-following stop-and-reverse lines computed from
volatility (Supertrend, ATR-banded around `(High+Low)/2`) or an accelerating
step function (Parabolic SAR) - each flips sides of price at a trend change
and otherwise trails it. Requiring both to agree - Supertrend flipping
bullish *and* price already above the SAR dots - cuts down on the
single-indicator whipsaws either one throws in a choppy tape.

Both are recursive (`supertrend[t]` and `psar[t]` depend on their own prior
value and, for SAR, an accelerating internal state), so they're computed
with explicit loops rather than vectorized pandas ops - the standard way
either indicator is implemented anywhere.
"""

import numpy as np
import pandas as pd

from ..indicators import wilder_atr
from .base import BaseStrategy


def supertrend(
    df: pd.DataFrame, period: int = 10, multiplier: float = 3.0
) -> pd.DataFrame:
    """Supertrend line and direction, indexed like `df`.

    Returns:
        DataFrame with columns `supertrend` (the trailing stop line) and
        `supertrend_direction` (`1` bullish / `-1` bearish). Both are NaN /
        `0` over the ATR warm-up window.

    Raises:
        ValueError: if `period` < 2 or `multiplier` <= 0.
    """
    if period < 2:
        raise ValueError("period must be at least 2")
    if multiplier <= 0:
        raise ValueError("multiplier must be positive")

    atr = wilder_atr(df, period)
    hl2 = (df["High"] + df["Low"]) / 2
    basic_upper = (hl2 + multiplier * atr).to_numpy(dtype=float)
    basic_lower = (hl2 - multiplier * atr).to_numpy(dtype=float)
    close = df["Close"].to_numpy(dtype=float)
    n = len(df)

    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    line = np.full(n, np.nan)
    direction = np.zeros(n, dtype=int)

    warm_up = (
        int(np.argmax(np.isfinite(basic_upper)))
        if np.any(np.isfinite(basic_upper))
        else n
    )
    if warm_up >= n:
        return pd.DataFrame(
            {"supertrend": line, "supertrend_direction": direction}, index=df.index
        )

    final_upper[warm_up] = basic_upper[warm_up]
    final_lower[warm_up] = basic_lower[warm_up]
    direction[warm_up] = 1 if close[warm_up] > final_upper[warm_up] else -1
    line[warm_up] = (
        final_lower[warm_up] if direction[warm_up] == 1 else final_upper[warm_up]
    )

    for t in range(warm_up + 1, n):
        final_upper[t] = (
            basic_upper[t]
            if basic_upper[t] < final_upper[t - 1] or close[t - 1] > final_upper[t - 1]
            else final_upper[t - 1]
        )
        final_lower[t] = (
            basic_lower[t]
            if basic_lower[t] > final_lower[t - 1] or close[t - 1] < final_lower[t - 1]
            else final_lower[t - 1]
        )

        if direction[t - 1] == 1:
            direction[t] = -1 if close[t] < final_lower[t] else 1
        else:
            direction[t] = 1 if close[t] > final_upper[t] else -1

        line[t] = final_lower[t] if direction[t] == 1 else final_upper[t]

    return pd.DataFrame(
        {"supertrend": line, "supertrend_direction": direction}, index=df.index
    )


def parabolic_sar(
    df: pd.DataFrame,
    af_start: float = 0.02,
    af_step: float = 0.02,
    af_max: float = 0.2,
) -> pd.Series:
    """Parabolic Stop-And-Reverse, indexed like `df`.

    Args:
        af_start: Initial acceleration factor.
        af_step: Acceleration-factor increment on each new extreme point.
        af_max: Acceleration-factor ceiling.

    Raises:
        ValueError: if `df` has fewer than 2 rows, or any `af_*` parameter
            is non-positive, or `af_start` exceeds `af_max`.
    """
    if len(df) < 2:
        raise ValueError("df must have at least 2 rows")
    if af_start <= 0 or af_step <= 0 or af_max <= 0:
        raise ValueError("af_start, af_step, af_max must all be positive")
    if af_start > af_max:
        raise ValueError("af_start must not exceed af_max")

    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    n = len(df)

    sar = np.full(n, np.nan)
    # Seed with an arbitrary-but-consistent uptrend start: the classic
    # implementation's initial state is a convention, not a derived value -
    # it self-corrects within the first few flips regardless of which side
    # it starts on.
    bullish = True
    extreme_point = high[0]
    af = af_start
    sar[0] = low[0]

    for t in range(1, n):
        prior_sar = sar[t - 1]
        candidate = prior_sar + af * (extreme_point - prior_sar)

        if bullish:
            candidate = min(candidate, low[t - 1], low[t - 2] if t >= 2 else low[t - 1])
            flipped = low[t] < candidate
        else:
            candidate = max(
                candidate, high[t - 1], high[t - 2] if t >= 2 else high[t - 1]
            )
            flipped = high[t] > candidate

        if flipped:
            sar[t] = extreme_point
            bullish = not bullish
            extreme_point = high[t] if bullish else low[t]
            af = af_start
        else:
            sar[t] = candidate
            new_extreme = high[t] if bullish else low[t]
            if bullish and new_extreme > extreme_point:
                extreme_point = new_extreme
                af = min(af + af_step, af_max)
            elif not bullish and new_extreme < extreme_point:
                extreme_point = new_extreme
                af = min(af + af_step, af_max)

    return pd.Series(sar, index=df.index, name="psar")


class SupertrendPSARStrategy(BaseStrategy):
    """Supertrend flip to bullish, confirmed by price already above PSAR.

    Args:
        supertrend_period: ATR/Supertrend smoothing period.
        supertrend_multiplier: Supertrend band width, in ATR multiples.
        psar_af_start: Parabolic SAR initial acceleration factor.
        psar_af_step: Parabolic SAR acceleration-factor increment.
        psar_af_max: Parabolic SAR acceleration-factor ceiling.
        atr_period: Wilder ATR smoothing period, for the ATR-based stop/target.
        sl_atr_multiplier: Stop-loss distance, in ATR.
        tp_atr_multiplier: Take-profit distance, in ATR.
    """

    def __init__(
        self,
        supertrend_period: int = 10,
        supertrend_multiplier: float = 3.0,
        psar_af_start: float = 0.02,
        psar_af_step: float = 0.02,
        psar_af_max: float = 0.2,
        atr_period: int = 14,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 3.5,
    ):
        super().__init__(name="Supertrend + Parabolic SAR Confluence")

        self.supertrend_period = supertrend_period
        self.supertrend_multiplier = supertrend_multiplier
        self.psar_af_start = psar_af_start
        self.psar_af_step = psar_af_step
        self.psar_af_max = psar_af_max
        self.atr_period = atr_period
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        st = supertrend(df, self.supertrend_period, self.supertrend_multiplier)
        df["supertrend"] = st["supertrend"]
        df["supertrend_direction"] = st["supertrend_direction"]
        df["psar"] = parabolic_sar(
            df, self.psar_af_start, self.psar_af_step, self.psar_af_max
        )
        df["atr"] = wilder_atr(df, self.atr_period)
        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """See BaseStrategy.generate_signals for the output schema."""
        df = self._calculate_indicators(df)

        flipped_bullish = (df["supertrend_direction"] == 1) & (
            df["supertrend_direction"].shift(1) == -1
        )
        above_psar = df["Close"] > df["psar"]

        warm_up = pd.Series(True, index=df.index)
        warm_up.iloc[: self.supertrend_period + 2] = False

        has_data = df["supertrend"].notna() & df["psar"].notna() & df["atr"].notna()

        buy = warm_up & has_data & flipped_bullish & above_psar

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
            f"SupertrendPSARStrategy(supertrend_period={self.supertrend_period}, "
            f"multiplier={self.supertrend_multiplier})"
        )
