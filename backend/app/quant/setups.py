"""
Latest-bar setup resolution for the live screener.

Turns a ticker's price history into one actionable row: the strategy's
signal on the most recent bar, the ADX regime it fired in, and - for long
entries - the sized order `RiskManager` would place.

On direction labelling, which is the part worth reading closely. The
execution engines here are long-only (`engine.py` opens on `signal == 1`
and closes on `signal == -1`; `forward_tester.py` hardcodes `direction=1`).
So:

  LONG       a buy signal, sized, tradable by every engine in this repo.
  EXIT_LONG  a sell signal. An exit for holders, *not* a short entry -
             sizing it as a short would print share counts for a position
             nothing here can open, model or journal.
  SHORT      a screening-only label: a bearish signal confirmed by a
             BEAR_TREND regime. Carries `tradable=False` and no share
             count, because no engine in this repo can execute it.
  FLAT       no signal on the latest bar.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from .regime import REGIME_BEAR_TREND, REGIME_UNKNOWN, RegimeDetector
from .risk import RiskManager
from .screener import CatalystFilter
from .strategies.base import BaseStrategy

DIRECTION_LONG = "LONG"
DIRECTION_SHORT = "SHORT"
DIRECTION_EXIT = "EXIT_LONG"
DIRECTION_FLAT = "FLAT"

#: Bars of history required before a ticker is scanned. Above the longest
#: warm-up of any registered strategy (DonchianBreakout's 63-bar momentum
#: window) with room for the ADX/ATR warm-up on top.
MIN_BARS = 100

#: Trailing bars `_check_liquidity`'s average-volume floor is measured over.
DEFAULT_VOLUME_LOOKBACK = 20

#: Minimum trailing average volume (shares/day) for a ticker to be scanned
#: at all - below this a setup isn't tradable at any real position size, so
#: there's no point running the strategy over it.
DEFAULT_MIN_AVG_VOLUME = 100_000.0


class ScanSkipError(ValueError):
    """Base for `scan_ticker` skip reasons `scan_universe` categorizes.

    Subclasses `ValueError` so a caller catching the older, coarser
    `(ValueError, KeyError)` pair still catches these - only `scan_universe`
    itself needs the finer-grained `reason` to sort skips into
    `ScanReport.skip_reasons` buckets a UI can render distinctly (a stale
    cache is an ops problem; insufficient history is just a new listing).
    """

    reason: str = "UNKNOWN"


class InsufficientHistoryError(ScanSkipError):
    reason = "INSUFFICIENT_HISTORY"


class DataStaleError(ScanSkipError):
    reason = "DATA_STALE"


class VolumeFilterFailedError(ScanSkipError):
    reason = "VOLUME_FILTER_FAILED"


class ZeroLiquidityError(ScanSkipError):
    reason = "ZERO_LIQUIDITY"


def _check_liquidity(
    ticker: str,
    df: pd.DataFrame,
    min_avg_volume: float | None,
    volume_lookback: int,
) -> None:
    """Raise `ZeroLiquidityError`/`VolumeFilterFailedError` for an
    unscoreable-by-liquidity ticker; otherwise return.

    Args:
        min_avg_volume: `None` disables the average-volume floor entirely
            (the zero-volume check still applies - that's never tradable).
    """
    latest_volume = float(df["Volume"].iloc[-1])
    if not math.isfinite(latest_volume) or latest_volume <= 0:
        raise ZeroLiquidityError(f"{ticker}: zero volume on the latest bar")

    if min_avg_volume is None:
        return

    lookback = min(volume_lookback, len(df))
    avg_volume = float(df["Volume"].iloc[-lookback:].mean())
    if not math.isfinite(avg_volume) or avg_volume < min_avg_volume:
        raise VolumeFilterFailedError(
            f"{ticker}: {avg_volume:,.0f}-share average volume over the "
            f"trailing {lookback} bars is below the {min_avg_volume:,.0f} "
            "liquidity floor"
        )


def _check_staleness(
    ticker: str,
    df: pd.DataFrame,
    stale_after_days: int | None,
    as_of_reference: pd.Timestamp | None,
) -> None:
    """Raise `DataStaleError` if `df`'s last bar is older than
    `stale_after_days`. A no-op when `stale_after_days` is `None`."""
    if stale_after_days is None:
        return

    reference = as_of_reference or pd.Timestamp.now().normalize()
    last_bar_date = pd.Timestamp(df.index[-1]).normalize()
    age_days = (reference - last_bar_date).days
    if age_days > stale_after_days:
        raise DataStaleError(
            f"{ticker}: last bar is {age_days} days old (as of "
            f"{reference.date()}), exceeds the {stale_after_days}-day "
            "staleness limit"
        )


@dataclass(frozen=True)
class Setup:
    """One screener row."""

    ticker: str
    direction: str
    tradable: bool
    as_of: pd.Timestamp
    close: float
    regime: str
    adx: float | None
    atr: float | None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    shares: int | None = None
    risk_amount: float | None = None
    relative_strength: float | None = None
    rank: int | None = None
    note: str | None = None
    reward_risk_ratio: float | None = None
    notional_value: float | None = None

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "direction": self.direction,
            "tradable": self.tradable,
            "as_of": self.as_of.strftime("%Y-%m-%d"),
            "close": _round(self.close, 4),
            "regime": self.regime,
            "adx": _round(self.adx, 2),
            "atr": _round(self.atr, 4),
            "entry_price": _round(self.entry_price, 4),
            "stop_loss": _round(self.stop_loss, 4),
            "take_profit": _round(self.take_profit, 4),
            "shares": self.shares,
            "risk_amount": _round(self.risk_amount, 2),
            "relative_strength": _round(self.relative_strength, 4),
            "rank": self.rank,
            "note": self.note,
            "reward_risk_ratio": _round(self.reward_risk_ratio, 2),
            "notional_value": _round(self.notional_value, 2),
        }


def _round(value: float | None, places: int) -> float | None:
    """Round, mapping NaN/inf to None so the row stays valid JSON."""
    if value is None:
        return None
    number = float(value)
    return round(number, places) if math.isfinite(number) else None


def scan_ticker(
    ticker: str,
    df: pd.DataFrame,
    strategy: BaseStrategy,
    risk_manager: RiskManager,
    regime_detector: RegimeDetector | None = None,
    catalyst_filter: CatalystFilter | None = None,
    earnings_dates: list[pd.Timestamp] | None = None,
    min_avg_volume: float | None = DEFAULT_MIN_AVG_VOLUME,
    volume_lookback: int = DEFAULT_VOLUME_LOOKBACK,
    stale_after_days: int | None = None,
    as_of_reference: pd.Timestamp | None = None,
) -> Setup:
    """Resolve `ticker`'s latest bar into a `Setup`.

    Args:
        catalyst_filter: When supplied together with `earnings_dates`, a
            LONG setup falling inside the filter's blackout window of the
            next earnings date is downgraded to non-tradable with an
            explanatory note, rather than sized normally. Off (`None`) by
            default so callers that don't have an earnings calendar handy
            get the same behavior as before this filter existed.
        earnings_dates: The ticker's known/estimated earnings report dates.
            Ignored if `catalyst_filter` is `None`.
        min_avg_volume: Minimum trailing average volume for the ticker to be
            scanned at all; `None` disables the floor (the zero-volume check
            still applies).
        volume_lookback: Trailing bar count `min_avg_volume` is averaged over.
        stale_after_days: Skip the ticker if its last bar is older than this
            many days. `None` (the default) disables the check.
        as_of_reference: Reference "today" the staleness check measures bar
            age against. Defaults to `pd.Timestamp.now()` - tests that
            exercise `stale_after_days` should pass this explicitly rather
            than depend on wall-clock time.

    Raises:
        InsufficientHistoryError: if `df` has fewer than `MIN_BARS` rows.
        ZeroLiquidityError: if the latest bar's volume is zero/non-finite.
        VolumeFilterFailedError: if trailing average volume is below
            `min_avg_volume`.
        DataStaleError: if the last bar is older than `stale_after_days`.
        ValueError: if the strategy emits a signal the risk manager cannot
            size. Callers scanning a heterogeneous universe should catch all
            of the above - `scan_universe` does.
    """
    if len(df) < MIN_BARS:
        raise InsufficientHistoryError(
            f"{ticker}: {len(df)} bars, need at least {MIN_BARS}"
        )
    _check_liquidity(ticker, df, min_avg_volume, volume_lookback)
    _check_staleness(ticker, df, stale_after_days, as_of_reference)

    detector = regime_detector or RegimeDetector()
    signals = strategy.generate_signals(df)
    BaseStrategy.validate_output(signals)

    latest = signals.iloc[-1]
    signal = int(latest["signal"])
    close = float(latest["Close"])
    atr = float(latest["atr"]) if "atr" in signals.columns else None

    indicators = detector.compute_indicators(df)
    adx = float(indicators["adx"].iloc[-1])
    regime = detector.current_regime(df)

    common = {
        "ticker": ticker,
        "as_of": signals.index[-1],
        "close": close,
        "regime": regime,
        "adx": adx if math.isfinite(adx) else None,
        "atr": atr if atr is not None and math.isfinite(atr) else None,
    }

    if signal == 1:
        order = risk_manager.build_order(
            entry_price=close,
            sl_type=str(latest["sl_type"]),
            sl_value=float(latest["sl_value"]),
            tp_type=str(latest["tp_type"]),
            tp_value=float(latest["tp_value"]),
            direction=1,
            atr=atr,
        )
        reward_risk_ratio = RiskManager.reward_to_risk(
            order.entry_price, order.stop_loss, order.take_profit
        )
        notional_value = order.shares * order.entry_price

        blocked_by_earnings = bool(
            catalyst_filter is not None
            and earnings_dates
            and catalyst_filter.is_blocked(signals.index[-1], earnings_dates)
        )
        if blocked_by_earnings:
            return Setup(
                direction=DIRECTION_LONG,
                tradable=False,
                entry_price=order.entry_price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                shares=0,
                risk_amount=0.0,
                reward_risk_ratio=reward_risk_ratio,
                notional_value=None,
                note=(
                    f"Earnings blackout: next print within "
                    f"{catalyst_filter.blackout_days} trading days - setup "
                    "suppressed."
                ),
                **common,
            )

        return Setup(
            direction=DIRECTION_LONG,
            tradable=order.shares > 0,
            entry_price=order.entry_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            shares=order.shares,
            risk_amount=order.risk_amount,
            reward_risk_ratio=reward_risk_ratio,
            notional_value=notional_value if order.shares > 0 else None,
            note=(
                None
                if order.shares > 0
                else "Stop distance exceeds the per-trade risk budget at this "
                "account size - sizing rounds to zero shares."
            ),
            **common,
        )

    if signal == -1:
        # A bearish signal in a confirmed downtrend is surfaced as a short
        # *candidate* for discretionary traders; anything else is just an
        # exit. Either way `tradable` is False - see the module docstring.
        is_short_candidate = regime == REGIME_BEAR_TREND
        return Setup(
            direction=DIRECTION_SHORT if is_short_candidate else DIRECTION_EXIT,
            tradable=False,
            entry_price=close,
            note=(
                "Screening only: the execution engines are long-only and "
                "cannot backtest or journal a short."
                if is_short_candidate
                else "Exit signal for an open long position."
            ),
            **common,
        )

    return Setup(direction=DIRECTION_FLAT, tradable=False, **common)


@dataclass
class ScanReport:
    """Result of scanning a universe, with the skips accounted for.

    Skips are counted rather than discarded so a systemic misconfiguration
    (every ticker uncached, a bad data directory) is distinguishable from a
    genuinely quiet market, which looks identical in the rows alone.
    """

    setups: list[Setup] = field(default_factory=list)
    skipped: int = 0
    skip_reasons: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict:
        return {
            "setups": [s.as_dict() for s in self.setups],
            "scanned": len(self.setups) + self.skipped,
            "skipped": self.skipped,
            "skip_reasons": dict(self.skip_reasons),
        }


def scan_universe(
    frames: dict[str, pd.DataFrame],
    strategy: BaseStrategy,
    risk_manager: RiskManager,
    regime_detector: RegimeDetector | None = None,
    include_flat: bool = False,
    catalyst_filter: CatalystFilter | None = None,
    earnings_by_ticker: dict[str, list[pd.Timestamp]] | None = None,
    min_avg_volume: float | None = DEFAULT_MIN_AVG_VOLUME,
    volume_lookback: int = DEFAULT_VOLUME_LOOKBACK,
    stale_after_days: int | None = None,
    as_of_reference: pd.Timestamp | None = None,
) -> ScanReport:
    """Scan every ticker in `frames`, tolerating per-ticker failures.

    Args:
        include_flat: Keep `FLAT` rows. Off by default - the screener grid
            wants setups, not the whole universe.
        catalyst_filter: Forwarded to `scan_ticker` for every ticker. `None`
            (the default) disables earnings-blackout suppression entirely.
        earnings_by_ticker: `{ticker: [earnings dates]}`. A ticker missing
            from this map is scanned with no earnings dates, i.e. never
            blocked - the same as an unscoreable/uncovered symbol.
        min_avg_volume: Forwarded to `scan_ticker`; `None` disables the
            liquidity floor.
        volume_lookback: Forwarded to `scan_ticker`.
        stale_after_days: Forwarded to `scan_ticker`; `None` disables the
            staleness check.
        as_of_reference: Forwarded to `scan_ticker`.
    """
    detector = regime_detector or RegimeDetector()
    earnings_by_ticker = earnings_by_ticker or {}
    report = ScanReport()

    for ticker, df in frames.items():
        try:
            setup = scan_ticker(
                ticker,
                df,
                strategy,
                risk_manager,
                detector,
                catalyst_filter=catalyst_filter,
                earnings_dates=earnings_by_ticker.get(ticker),
                min_avg_volume=min_avg_volume,
                volume_lookback=volume_lookback,
                stale_after_days=stale_after_days,
                as_of_reference=as_of_reference,
            )
        except ScanSkipError as exc:
            report.skipped += 1
            report.skip_reasons[exc.reason] += 1
            continue
        except (ValueError, KeyError) as exc:
            report.skipped += 1
            report.skip_reasons[type(exc).__name__] += 1
            continue

        if setup.direction == DIRECTION_FLAT and not include_flat:
            report.skipped += 1
            report.skip_reasons["no_signal"] += 1
            continue

        report.setups.append(setup)

    # Actionable longs first, then short candidates, then exits; within a
    # direction, strongest trend first.
    order = {
        DIRECTION_LONG: 0,
        DIRECTION_SHORT: 1,
        DIRECTION_EXIT: 2,
        DIRECTION_FLAT: 3,
    }
    report.setups.sort(
        key=lambda s: (
            order.get(s.direction, 9),
            not s.tradable,
            -(s.adx if s.adx is not None else 0.0),
        )
    )
    return report


def annotate_relative_strength(
    setups: list[Setup], ranking: pd.DataFrame
) -> list[Setup]:
    """Attach `relative_strength` and `rank` from a screener ranking frame.

    Args:
        ranking: Output of `RelativeStrengthScreener.rank` - columns
            `ticker`, `relative_strength`, `rank`.
    """
    if ranking.empty:
        return setups

    indexed = ranking.set_index("ticker")
    annotated = []
    for setup in setups:
        if setup.ticker not in indexed.index:
            annotated.append(setup)
            continue
        row = indexed.loc[setup.ticker]
        annotated.append(
            Setup(
                **{
                    **{
                        k: getattr(setup, k)
                        for k in setup.__dataclass_fields__
                        if k not in ("relative_strength", "rank")
                    },
                    "relative_strength": float(row["relative_strength"]),
                    "rank": int(row["rank"]),
                }
            )
        )
    return annotated


def regime_label(regime: str | None) -> str:
    """Display-safe regime string."""
    return regime or REGIME_UNKNOWN
