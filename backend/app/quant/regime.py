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

import math

import numpy as np
import pandas as pd

from .indicators import true_range, wilder_atr

REGIME_BULL_TREND = "BULL_TREND"
REGIME_BEAR_TREND = "BEAR_TREND"
REGIME_CHOPPY = "CHOPPY"
REGIME_UNKNOWN = "UNKNOWN"

#: Macro (SPY-level) regime states - distinct from the per-ticker ADX states
#: above. `MacroRegimeDetector` is the "market regime engine": a top-down
#: filter meant to gate *every* ticker's long setups at once, not to label
#: one ticker's own trend.
MACRO_BULL_TRENDING = "BULL_TRENDING"
MACRO_BEAR_TRENDING = "BEAR_TRENDING"
MACRO_HIGH_VOLATILITY_CHOP = "HIGH_VOLATILITY_CHOP"
MACRO_NEUTRAL = "NEUTRAL"
MACRO_REGIME_UNKNOWN = "UNKNOWN"

#: Macro states in which new long setups should be suppressed/gated.
MACRO_BLOCKED_REGIMES = frozenset({MACRO_BEAR_TRENDING, MACRO_HIGH_VOLATILITY_CHOP})

#: Trading days per year, for annualising the rolling volatility measure.
_TRADING_DAYS_PER_YEAR = 252


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


class MacroRegimeDetector:
    """Top-down market regime classifier, meant to run on a broad index
    (SPY) and gate every ticker's long setups at once.

    Combines a trend filter (50/200-day SMA relationship plus price vs. the
    50-day SMA) with a volatility filter (annualised rolling realised
    volatility) into one of four states:

        BULL_TRENDING          50-SMA > 200-SMA, price above the 50-SMA,
                                volatility not elevated.
        BEAR_TRENDING          50-SMA < 200-SMA, price below the 50-SMA,
                                volatility not elevated.
        HIGH_VOLATILITY_CHOP   Rolling volatility above
                                `high_vol_annualized_threshold` - takes
                                priority over the trend read, since an
                                index whipsawing hard enough to trip this is
                                not a market to be pressing new longs into
                                even if the moving averages still say "bull".
        NEUTRAL                Neither trend condition holds and volatility
                                is not elevated (e.g. the SMAs are crossing,
                                or price is chopping around the 50-SMA).

    Args:
        sma_fast_period: Faster trend SMA, e.g. 50.
        sma_slow_period: Slower trend SMA, e.g. 200.
        volatility_period: Rolling window (in bars) for realised volatility.
        high_vol_annualized_threshold: Annualised rolling volatility above
            which the regime is called `HIGH_VOLATILITY_CHOP` regardless of
            trend, e.g. `0.20` for 20%. SPY's long-run annualised volatility
            sits roughly in the mid-teens percent; this default sits above
            that baseline and below crisis-level readings (30%+).
    """

    def __init__(
        self,
        sma_fast_period: int = 50,
        sma_slow_period: int = 200,
        volatility_period: int = 20,
        high_vol_annualized_threshold: float = 0.20,
    ):
        if sma_fast_period < 2:
            raise ValueError("sma_fast_period must be at least 2")
        if sma_slow_period <= sma_fast_period:
            raise ValueError("sma_slow_period must be greater than sma_fast_period")
        if volatility_period < 2:
            raise ValueError("volatility_period must be at least 2")
        if high_vol_annualized_threshold <= 0:
            raise ValueError("high_vol_annualized_threshold must be positive")

        self.sma_fast_period = sma_fast_period
        self.sma_slow_period = sma_slow_period
        self.volatility_period = volatility_period
        self.high_vol_annualized_threshold = high_vol_annualized_threshold

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a copy of `df` with `sma_fast`, `sma_slow`, and
        `volatility_annualized` added.

        Raises:
            ValueError: if `df` is empty or missing `Close`.
        """
        if "Close" not in df.columns:
            raise ValueError("df is missing required column: 'Close'")
        if df.empty:
            raise ValueError("df is empty")

        df = df.copy()
        close = df["Close"]

        df["sma_fast"] = close.rolling(
            window=self.sma_fast_period, min_periods=self.sma_fast_period
        ).mean()
        df["sma_slow"] = close.rolling(
            window=self.sma_slow_period, min_periods=self.sma_slow_period
        ).mean()

        daily_returns = close.pct_change()
        df["volatility_annualized"] = daily_returns.rolling(
            window=self.volatility_period, min_periods=self.volatility_period
        ).std(ddof=1) * math.sqrt(_TRADING_DAYS_PER_YEAR)

        return df

    def detect_regime(self, df: pd.DataFrame) -> pd.Series:
        """Per-bar macro regime classification, indexed like `df`.

        Bars still inside the slow SMA's warm-up window are `None`.
        """
        indicators = self.compute_indicators(df)

        close = indicators["Close"]
        sma_fast = indicators["sma_fast"]
        sma_slow = indicators["sma_slow"]
        volatility = indicators["volatility_annualized"]

        high_vol = volatility > self.high_vol_annualized_threshold
        bull_trend = (sma_fast > sma_slow) & (close > sma_fast)
        bear_trend = (sma_fast < sma_slow) & (close < sma_fast)

        regime = pd.Series(MACRO_NEUTRAL, index=df.index, dtype=object)
        regime[bull_trend] = MACRO_BULL_TRENDING
        regime[bear_trend] = MACRO_BEAR_TRENDING
        # Volatility gates last, so it overrides a trend read rather than
        # being overridden by one.
        regime[high_vol] = MACRO_HIGH_VOLATILITY_CHOP

        undefined = sma_slow.isna() | volatility.isna()
        regime[undefined] = None
        return regime

    def current_regime(self, df: pd.DataFrame) -> str:
        """Latest-bar macro regime, or `'UNKNOWN'` if there isn't enough
        history for the slow SMA / volatility window yet."""
        if len(df) < max(self.sma_slow_period, self.volatility_period):
            return MACRO_REGIME_UNKNOWN

        regime = self.detect_regime(df)
        latest = regime.iloc[-1]
        return latest if latest is not None else MACRO_REGIME_UNKNOWN

    def is_long_blocked(self, df: pd.DataFrame) -> bool:
        """Whether the current macro regime should suppress new long setups.

        Blocks on `BEAR_TRENDING` and `HIGH_VOLATILITY_CHOP`; does *not*
        block on `UNKNOWN` (insufficient history) - an unclassified macro
        regime is a reason to fall back on other filters, not to halt
        trading outright the way the drawdown circuit breaker does.
        """
        return self.current_regime(df) in MACRO_BLOCKED_REGIMES
