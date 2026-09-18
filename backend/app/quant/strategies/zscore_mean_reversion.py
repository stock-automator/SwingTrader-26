"""
Z-Score Mean Reversion with Hurst Exponent Filter.

Trades a pullback to statistical extremes only when the *regime* itself is
actually mean-reverting: a rolling z-score of price against its own mean
flags "how far from normal," but a stock trending hard away from its mean
will keep printing large z-scores without ever reverting, so the z-score
alone is a fine way to get run over. The Hurst exponent filters that out - a
value below 0.5 indicates a mean-reverting series (price diffusion is
sub-random-walk), above 0.5 indicates trending/persistent behavior, and
0.5 is a pure random walk. Only oversold pullbacks in a sub-0.5 regime are
traded.

Hurst is estimated with the standard rescaled-variance shortcut (the
`log(lag) vs log(std of the lag-differenced series)` regression slope,
which *is* the Hurst exponent for fractional Brownian motion) rather than
full R/S analysis - accurate enough to classify "reverting vs. trending"
without pulling in a stats dependency, which is all this filter needs it
for.
"""

import numpy as np
import pandas as pd

from ..indicators import wilder_atr
from .base import BaseStrategy

#: Lags (in bars) the Hurst regression is fit over. `range(2, 20)` is the
#: conventional window - short enough to stay inside a typical rolling
#: estimation window, long enough for the regression to be meaningful.
DEFAULT_HURST_LAGS = tuple(range(2, 20))


def hurst_exponent(series: np.ndarray, lags=DEFAULT_HURST_LAGS) -> float:
    """Hurst exponent of a 1-D array via the lag-variance method.

    Returns:
        `0.5` (a random walk - the neutral/uninformative default) if `series`
        is too short or too flat for the regression to be well-posed, rather
        than raising - this runs inside a rolling `.apply`, where a
        degenerate window must not blow up the whole rolling computation.
    """
    series = np.asarray(series, dtype=float)
    max_lag = max(lags)
    if len(series) <= max_lag:
        return 0.5

    tau = []
    valid_lags = []
    for lag in lags:
        diffs = series[lag:] - series[:-lag]
        std = np.std(diffs)
        if std > 0 and np.isfinite(std):
            tau.append(std)
            valid_lags.append(lag)

    if len(valid_lags) < 2:
        return 0.5

    log_lags = np.log(valid_lags)
    log_tau = np.log(tau)
    # std(diff(series, lag)) scales as lag**H for fractional Brownian motion
    # (H=0.5 recovers the random-walk lag**0.5 rule), so the log-log slope
    # *is* the Hurst exponent directly - no factor-of-2 correction needed.
    slope, _ = np.polyfit(log_lags, log_tau, 1)
    if not np.isfinite(slope):
        return 0.5
    return float(np.clip(slope, 0.0, 1.0))


def rolling_hurst(
    close: pd.Series, window: int = 100, lags=DEFAULT_HURST_LAGS
) -> pd.Series:
    """Hurst exponent over a trailing `window` of `close`, indexed like
    `close`. NaN over the warm-up window."""
    if window <= max(lags):
        raise ValueError("window must be greater than max(lags)")

    return close.rolling(window).apply(lambda w: hurst_exponent(w, lags), raw=True)


class ZScoreMeanReversionStrategy(BaseStrategy):
    """Oversold z-score pullback, gated by a mean-reverting Hurst regime.

    Args:
        zscore_period: Rolling window the price z-score is measured over.
        entry_zscore: Buy when the z-score drops to or below `-entry_zscore`
            (e.g. `2.0` -> 2 standard deviations below the rolling mean).
        hurst_window: Rolling window the Hurst exponent is estimated over.
            Must exceed the largest lag in `hurst_lags`.
        hurst_threshold: Regime gate - only trade while the rolling Hurst
            exponent is at or below this (below `0.5` is mean-reverting).
        atr_period: Wilder ATR smoothing period, for the ATR-based stop/target.
        sl_atr_multiplier: Stop-loss distance, in ATR. Kept tight relative to
            the trend-following strategies here - a mean-reversion entry
            that keeps falling has already disproven its own premise.
        tp_atr_multiplier: Take-profit distance, in ATR.
    """

    def __init__(
        self,
        zscore_period: int = 20,
        entry_zscore: float = 2.0,
        hurst_window: int = 100,
        hurst_threshold: float = 0.5,
        atr_period: int = 14,
        sl_atr_multiplier: float = 1.5,
        tp_atr_multiplier: float = 3.0,
    ):
        super().__init__(name="Z-Score Mean Reversion (Hurst-Filtered)")

        if zscore_period < 2:
            raise ValueError("zscore_period must be at least 2")
        if entry_zscore <= 0:
            raise ValueError("entry_zscore must be positive")
        if hurst_window <= max(DEFAULT_HURST_LAGS):
            raise ValueError(f"hurst_window must exceed {max(DEFAULT_HURST_LAGS)}")
        if not 0.0 <= hurst_threshold <= 1.0:
            raise ValueError("hurst_threshold must be between 0.0 and 1.0")

        self.zscore_period = zscore_period
        self.entry_zscore = entry_zscore
        self.hurst_window = hurst_window
        self.hurst_threshold = hurst_threshold
        self.atr_period = atr_period
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_atr_multiplier = tp_atr_multiplier

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        close = df["Close"]

        mean = close.rolling(self.zscore_period).mean()
        std = close.rolling(self.zscore_period).std()
        df["zscore"] = (close - mean) / std.replace(0, np.nan)
        df["hurst"] = rolling_hurst(close, self.hurst_window)
        df["atr"] = wilder_atr(df, self.atr_period)
        return df

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """See BaseStrategy.generate_signals for the output schema."""
        df = self._calculate_indicators(df)

        oversold = df["zscore"] <= -self.entry_zscore
        mean_reverting_regime = df["hurst"] <= self.hurst_threshold

        warm_up = pd.Series(True, index=df.index)
        warm_up.iloc[: self.hurst_window] = False

        has_data = df["zscore"].notna() & df["hurst"].notna() & df["atr"].notna()

        buy = warm_up & has_data & oversold & mean_reverting_regime

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
            f"ZScoreMeanReversionStrategy(zscore_period={self.zscore_period}, "
            f"entry_zscore={self.entry_zscore}, hurst_window={self.hurst_window})"
        )
