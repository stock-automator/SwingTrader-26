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
from .strategies.base import BaseStrategy

DIRECTION_LONG = "LONG"
DIRECTION_SHORT = "SHORT"
DIRECTION_EXIT = "EXIT_LONG"
DIRECTION_FLAT = "FLAT"

#: Bars of history required before a ticker is scanned. Above the longest
#: warm-up of any registered strategy (DonchianBreakout's 63-bar momentum
#: window) with room for the ADX/ATR warm-up on top.
MIN_BARS = 100


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
) -> Setup:
    """Resolve `ticker`'s latest bar into a `Setup`.

    Raises:
        ValueError: if `df` has fewer than `MIN_BARS` rows, or the strategy
            emits a signal the risk manager cannot size. Callers scanning a
            heterogeneous universe should catch this - `scan_universe` does.
    """
    if len(df) < MIN_BARS:
        raise ValueError(f"{ticker}: {len(df)} bars, need at least {MIN_BARS}")

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
        return Setup(
            direction=DIRECTION_LONG,
            tradable=order.shares > 0,
            entry_price=order.entry_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            shares=order.shares,
            risk_amount=order.risk_amount,
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
) -> ScanReport:
    """Scan every ticker in `frames`, tolerating per-ticker failures.

    Args:
        include_flat: Keep `FLAT` rows. Off by default - the screener grid
            wants setups, not the whole universe.
    """
    detector = regime_detector or RegimeDetector()
    report = ScanReport()

    for ticker, df in frames.items():
        try:
            setup = scan_ticker(ticker, df, strategy, risk_manager, detector)
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
