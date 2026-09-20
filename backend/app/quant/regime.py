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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

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


# ---------------------------------------------------------------------------
# Top-down market regime & breadth "traffic light"
#
# `MarketRegimeEngine` sits a level above `MacroRegimeDetector`: instead of
# reading one index's own trend, it combines SPY *and* QQQ EMA alignment,
# S&P 500 breadth (% of constituents above their own 50-EMA), and the VIX's
# volatility regime into one global `MarketHealthState` meant to gate every
# ticker's long setups at once (`GET /api/v1/market/regime`).
# ---------------------------------------------------------------------------

EMA_ALIGNMENT_BULLISH = "BULLISH"
EMA_ALIGNMENT_BEARISH = "BEARISH"
EMA_ALIGNMENT_NEUTRAL = "NEUTRAL"
EMA_ALIGNMENT_UNKNOWN = "UNKNOWN"

VIX_LOW = "LOW"
VIX_NORMAL = "NORMAL"
VIX_HIGH = "HIGH"
VIX_EXTREME = "EXTREME"
VIX_UNKNOWN = "UNKNOWN"

#: Market Health "traffic light" states `MarketRegimeEngine.classify`
#: returns - the single value the Dashboard's status badge renders.
HEALTH_BULL_CONFIRMED = "BULL_CONFIRMED"
HEALTH_CAUTION_CHOP = "CAUTION_CHOP"
HEALTH_BEAR_DEFENSIVE = "BEAR_DEFENSIVE"

#: VIX close-price breakpoints separating LOW/NORMAL/HIGH/EXTREME. Roughly:
#: sub-15 is a complacent tape, 15-20 is the long-run "normal" range, 20-30
#: is elevated/fearful, 30+ is crisis-level (2020 COVID crash, 2008 GFC).
DEFAULT_VIX_LOW_MAX = 15.0
DEFAULT_VIX_NORMAL_MAX = 20.0
DEFAULT_VIX_HIGH_MAX = 30.0

#: Breadth thresholds (% of universe above its own 50-EMA) gating
#: `BULL_CONFIRMED` (must be at/above) and `BEAR_DEFENSIVE` (at/below).
DEFAULT_BREADTH_BULL_THRESHOLD = 60.0
DEFAULT_BREADTH_BEAR_THRESHOLD = 40.0


@dataclass(frozen=True)
class BreadthResult:
    """S&P-500-style market breadth: what fraction of a universe is
    trading above its own 50-EMA right now."""

    pct_above_50ema: float
    above: int
    below: int
    unscored: int

    @property
    def total_scored(self) -> int:
        return self.above + self.below


@dataclass(frozen=True)
class MarketHealthReport:
    """Full `GET /api/v1/market/regime` payload: the top-level traffic-light
    state plus every metric that fed it, for the Dashboard badge's
    tooltip."""

    state: str
    spy_alignment: str
    qqq_alignment: str
    vix_level: float | None
    vix_regime: str
    breadth_pct: float | None
    breadth_above: int
    breadth_total: int
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "spy_alignment": self.spy_alignment,
            "qqq_alignment": self.qqq_alignment,
            "vix_level": self.vix_level,
            "vix_regime": self.vix_regime,
            "breadth_pct": self.breadth_pct,
            "breadth_above": self.breadth_above,
            "breadth_total": self.breadth_total,
            # Copied, not a reference: `api/market.py` caches this exact
            # dict and hands it out to every caller during the TTL window -
            # a shared mutable list would let one caller's in-place mutation
            # corrupt what every other concurrent reader sees.
            "notes": list(self.notes),
        }


class MarketRegimeEngine:
    """Top-down market health classifier: SPY/QQQ 50/200-EMA alignment +
    S&P 500 breadth + VIX volatility regime -> one `MarketHealthReport`.

    Every method here is a pure function of the frames/values passed in -
    no I/O - so it can be unit tested without a network or a live universe.
    `GET /api/v1/market/regime` (`api/market.py`) is the thin adapter that
    resolves those inputs via `data.loader`/`api.deps` and calls this.

    Args:
        ema_fast_period: Faster trend EMA, e.g. 50.
        ema_slow_period: Slower trend EMA, e.g. 200.
        breadth_ema_period: EMA period each breadth constituent is scored
            against, e.g. 50 (same period as `ema_fast_period` by default,
            but independently configurable).
        breadth_bull_threshold: Minimum breadth %% for `BULL_CONFIRMED`.
        breadth_bear_threshold: Breadth %% at/below which `BEAR_DEFENSIVE`
            is forced regardless of trend/VIX.
        vix_low_max / vix_normal_max / vix_high_max: VIX close-price
            breakpoints - see the `DEFAULT_VIX_*` constants.
    """

    def __init__(
        self,
        ema_fast_period: int = 50,
        ema_slow_period: int = 200,
        breadth_ema_period: int = 50,
        breadth_bull_threshold: float = DEFAULT_BREADTH_BULL_THRESHOLD,
        breadth_bear_threshold: float = DEFAULT_BREADTH_BEAR_THRESHOLD,
        vix_low_max: float = DEFAULT_VIX_LOW_MAX,
        vix_normal_max: float = DEFAULT_VIX_NORMAL_MAX,
        vix_high_max: float = DEFAULT_VIX_HIGH_MAX,
    ):
        if ema_fast_period < 2:
            raise ValueError("ema_fast_period must be at least 2")
        if ema_slow_period <= ema_fast_period:
            raise ValueError("ema_slow_period must be greater than ema_fast_period")
        if breadth_ema_period < 2:
            raise ValueError("breadth_ema_period must be at least 2")
        if not 0 <= breadth_bear_threshold < breadth_bull_threshold <= 100:
            raise ValueError(
                "require 0 <= breadth_bear_threshold < breadth_bull_threshold <= 100"
            )
        if not 0 < vix_low_max < vix_normal_max < vix_high_max:
            raise ValueError("require 0 < vix_low_max < vix_normal_max < vix_high_max")

        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period
        self.breadth_ema_period = breadth_ema_period
        self.breadth_bull_threshold = breadth_bull_threshold
        self.breadth_bear_threshold = breadth_bear_threshold
        self.vix_low_max = vix_low_max
        self.vix_normal_max = vix_normal_max
        self.vix_high_max = vix_high_max

    # ------------------------------------------------------------------
    # Per-index EMA alignment
    # ------------------------------------------------------------------

    def ema_alignment(self, df: pd.DataFrame) -> str:
        """Trend alignment for one index (SPY/QQQ): `BULLISH` when the fast
        EMA sits above the slow EMA *and* price is above the fast EMA,
        `BEARISH` on the mirror condition, else `NEUTRAL`. `UNKNOWN` if
        there isn't enough history for the slow EMA yet.
        """
        if df is None or df.empty or "Close" not in df.columns:
            return EMA_ALIGNMENT_UNKNOWN
        if len(df) < self.ema_slow_period:
            return EMA_ALIGNMENT_UNKNOWN

        close = df["Close"]
        ema_fast = close.ewm(span=self.ema_fast_period, adjust=False).mean()
        ema_slow = close.ewm(span=self.ema_slow_period, adjust=False).mean()

        last_close = float(close.iloc[-1])
        last_fast = float(ema_fast.iloc[-1])
        last_slow = float(ema_slow.iloc[-1])
        if any(math.isnan(v) for v in (last_close, last_fast, last_slow)):
            return EMA_ALIGNMENT_UNKNOWN

        if last_fast > last_slow and last_close > last_fast:
            return EMA_ALIGNMENT_BULLISH
        if last_fast < last_slow and last_close < last_fast:
            return EMA_ALIGNMENT_BEARISH
        return EMA_ALIGNMENT_NEUTRAL

    # ------------------------------------------------------------------
    # VIX volatility regime
    # ------------------------------------------------------------------

    def vix_regime(self, vix_level: float | None) -> str:
        """Bucket a VIX closing level into LOW/NORMAL/HIGH/EXTREME.

        `None` (VIX unavailable) maps to `UNKNOWN` - fails open, the same
        convention `MacroRegimeDetector.current_regime` uses for
        insufficient history.
        """
        if vix_level is None or not math.isfinite(vix_level):
            return VIX_UNKNOWN
        if vix_level < self.vix_low_max:
            return VIX_LOW
        if vix_level < self.vix_normal_max:
            return VIX_NORMAL
        if vix_level < self.vix_high_max:
            return VIX_HIGH
        return VIX_EXTREME

    # ------------------------------------------------------------------
    # Breadth
    # ------------------------------------------------------------------

    def _is_above_ema(self, df: pd.DataFrame) -> bool | None:
        """Whether the latest close is above its own `breadth_ema_period`
        EMA. `None` if the ticker has too little history to score."""
        if df is None or df.empty or "Close" not in df.columns:
            return None
        if len(df) < self.breadth_ema_period:
            return None

        close = df["Close"]
        ema = close.ewm(span=self.breadth_ema_period, adjust=False).mean()
        last_close = float(close.iloc[-1])
        last_ema = float(ema.iloc[-1])
        if math.isnan(last_close) or math.isnan(last_ema):
            return None
        return last_close > last_ema

    def compute_breadth(
        self, frames: dict[str, pd.DataFrame], max_workers: int = 16
    ) -> BreadthResult:
        """Percentage of `frames` currently trading above their own 50-EMA.

        Scored across a bounded `ThreadPoolExecutor` (matching
        `api.deps.load_frames`'s concurrency model) rather than a serial
        loop - each per-ticker EMA is cheap, but a several-hundred-name
        universe still adds up serially. Frames with too little history are
        excluded from the percentage (`unscored`), not counted as "below".
        """
        if not frames:
            return BreadthResult(pct_above_50ema=0.0, above=0, below=0, unscored=0)

        workers = max(1, min(max_workers, len(frames)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(self._is_above_ema, frames.values()))

        above = sum(1 for r in results if r is True)
        below = sum(1 for r in results if r is False)
        unscored = sum(1 for r in results if r is None)
        total_scored = above + below
        pct = (above / total_scored * 100.0) if total_scored else 0.0

        return BreadthResult(
            pct_above_50ema=pct, above=above, below=below, unscored=unscored
        )

    # ------------------------------------------------------------------
    # Top-level classification
    # ------------------------------------------------------------------

    def classify(
        self,
        spy_df: pd.DataFrame | None,
        qqq_df: pd.DataFrame | None,
        vix_level: float | None,
        breadth: BreadthResult | None,
    ) -> MarketHealthReport:
        """Combine SPY/QQQ alignment, VIX regime, and breadth into one
        `MarketHealthReport`.

        Precedence (first match wins), most-defensive first - any single
        strongly bearish signal is enough to force `BEAR_DEFENSIVE` even if
        the others look fine, the same "any gate fails -> reject" posture
        `RiskManager.build_risk_managed_order` takes for trade entries:

        1. `BEAR_DEFENSIVE` - SPY or QQQ in bearish EMA alignment, or VIX in
           `EXTREME`, or breadth at/below `breadth_bear_threshold`.
        2. `BULL_CONFIRMED` - SPY *and* QQQ bullish, VIX not `HIGH`/
           `EXTREME`, and breadth at/above `breadth_bull_threshold`.
        3. `CAUTION_CHOP` - everything else (mixed signals).
        """
        notes: list[str] = []
        spy_alignment = self.ema_alignment(spy_df)
        qqq_alignment = self.ema_alignment(qqq_df)
        vix_bucket = self.vix_regime(vix_level)
        breadth = breadth or BreadthResult(
            pct_above_50ema=0.0, above=0, below=0, unscored=0
        )
        breadth_pct = breadth.pct_above_50ema if breadth.total_scored else None

        def _report(state: str) -> MarketHealthReport:
            return MarketHealthReport(
                state=state,
                spy_alignment=spy_alignment,
                qqq_alignment=qqq_alignment,
                vix_level=vix_level,
                vix_regime=vix_bucket,
                breadth_pct=breadth_pct,
                breadth_above=breadth.above,
                breadth_total=breadth.total_scored,
                notes=notes,
            )

        bearish_alignment = EMA_ALIGNMENT_BEARISH in (spy_alignment, qqq_alignment)
        if bearish_alignment:
            notes.append("SPY/QQQ EMA alignment is bearish")
        if vix_bucket == VIX_EXTREME:
            notes.append(f"VIX regime is EXTREME ({vix_level})")
        if breadth_pct is not None and breadth_pct <= self.breadth_bear_threshold:
            notes.append(
                f"Breadth {breadth_pct:.1f}% at/below the "
                f"{self.breadth_bear_threshold:.0f}% bear threshold"
            )
        if (
            bearish_alignment
            or vix_bucket == VIX_EXTREME
            or (breadth_pct is not None and breadth_pct <= self.breadth_bear_threshold)
        ):
            return _report(HEALTH_BEAR_DEFENSIVE)

        bullish_alignment = spy_alignment == EMA_ALIGNMENT_BULLISH and (
            qqq_alignment == EMA_ALIGNMENT_BULLISH
        )
        vix_calm = vix_bucket in (VIX_LOW, VIX_NORMAL)
        breadth_strong = (
            breadth_pct is not None and breadth_pct >= self.breadth_bull_threshold
        )
        if bullish_alignment and vix_calm and breadth_strong:
            return _report(HEALTH_BULL_CONFIRMED)

        notes.append("Mixed signals: neither confirmed bull nor defensive bear")
        return _report(HEALTH_CAUTION_CHOP)
