"""
Order-dispatch safety guards, run by every order route immediately before
an order reaches a broker (`api/execution.py`, `api/orders.py`).

`enforce_order_guards` is the single entry point. An EXIT always passes and
makes no lookups - a guard must never stop risk coming *off*. `intent_for`
decides which an order is: only a sell of a long the account holds is an
EXIT, never on the caller's say-so. An ENTRY must pass both gates, and fails
closed if either cannot be checked:

- Session: the Alpaca trading calendar (`execution.market_clock`) must
  report a REGULAR session - holidays, weekends and the time after a
  half-day's early close are all closed. A calendar error blocks the entry.
- `EarningsLockoutGuard`: no earnings release or stock split within
  `lockout_hours` (default 48) either side. A lookup error blocks the entry;
  an empty calendar (e.g. an ETF) is "nothing to block on".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Literal

import pandas as pd

from ..data.loader import fetch_earnings_dates, fetch_stock_splits

if TYPE_CHECKING:
    from ..config import Settings
    from .market_clock import MarketClock

OrderIntent = Literal["ENTRY", "EXIT"]

log = logging.getLogger(__name__)

#: Suppress a new entry within this many hours of a scheduled earnings
#: release or stock split, on either side of it.
DEFAULT_EARNINGS_LOCKOUT_HOURS = 48


@dataclass(frozen=True)
class EarningsLockoutGuard:
    """Blocks a new entry within `lockout_hours` of a known earnings date or
    stock split, on either side of it.

    Unlike `quant.screener.CatalystFilter` (trading-day distance, blackout
    only *before* an upcoming print, screening-time only), this is a
    calendar-hour distance in both directions, meant to run at dispatch time
    against both earnings and split calendars together.

    Args:
        lockout_hours: Hours before *and* after a catalyst date during which
            a new entry is blocked, e.g. `48`.

    Raises:
        ValueError: if `lockout_hours` is negative.
    """

    lockout_hours: int = DEFAULT_EARNINGS_LOCKOUT_HOURS

    def __post_init__(self) -> None:
        if self.lockout_hours < 0:
            raise ValueError("lockout_hours must be non-negative")

    def is_blocked(
        self,
        as_of: pd.Timestamp | datetime,
        earnings_dates: list[pd.Timestamp] | None,
        split_dates: list[pd.Timestamp] | None = None,
    ) -> tuple[bool, str | None]:
        """Whether an entry at `as_of` falls inside the lockout window of
        any date in `earnings_dates` or `split_dates`.

        `None`/empty calendars mean nothing to block on. A calendar that
        could not be fetched is the caller's problem: `enforce_order_guards`
        fails closed on a lookup error before ever reaching this.
        """
        as_of = pd.Timestamp(as_of)
        if as_of.tzinfo is not None:
            # Catalyst dates come from `data.loader.fetch_earnings_dates`/
            # `fetch_stock_splits`, both tz-naive by contract - compare on
            # that same naive basis rather than raising on a tz mismatch.
            as_of = as_of.tz_localize(None)
        window = timedelta(hours=self.lockout_hours)

        for label, dates in (("earnings", earnings_dates), ("split", split_dates)):
            for catalyst_date in dates or []:
                catalyst_date = pd.Timestamp(catalyst_date)
                if abs(as_of - catalyst_date) <= window:
                    return True, (
                        f"Within {self.lockout_hours}h of a scheduled {label} "
                        f"date ({catalyst_date.date()}) - new entries suppressed."
                    )
        return False, None


@dataclass(frozen=True)
class GuardResult:
    """Combined outcome of every guard `enforce_order_guards` ran."""

    allowed: bool
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reasons": self.reasons}


def intent_for(
    ticker: str,
    side: str,
    qty: float,
    held_positions: Callable[[], list[dict]] | None,
) -> OrderIntent:
    """EXIT only for a sell the account can prove reduces a long: it holds
    at least `qty` long of `ticker` per `held_positions()` (Alpaca's
    `get_positions` shape). Everything else is an ENTRY - every buy, a sell
    with no position source (PAPER keeps none), an unheld, oversized or
    short-side sell, and a sell whose position lookup fails."""
    if side.lower() != "sell" or held_positions is None:
        return "ENTRY"
    try:
        positions = held_positions()
    except Exception as exc:  # fail closed: an unproven exit is an entry
        log.warning("order guard: position lookup failed for %s: %s", ticker, exc)
        return "ENTRY"
    symbol = ticker.upper()
    held = sum(
        float(p.get("qty") or 0)
        for p in positions
        if str(p.get("symbol", "")).upper() == symbol and p.get("side") == "long"
    )
    return "EXIT" if held >= qty > 0 else "ENTRY"


def _session_reasons(
    now: pd.Timestamp, settings: Settings, clock: MarketClock
) -> list[str]:
    try:
        info = clock.session(now.to_pydatetime())
    except Exception as exc:  # any calendar failure blocks a new entry
        log.warning("order guard: market calendar unavailable: %s", exc)
        return [
            "Market calendar unavailable - new entries are blocked "
            f"({type(exc).__name__})."
        ]
    if info.state == "REGULAR":
        return []
    if info.state == "EXTENDED" and settings.execution_allow_extended_hours:
        return []
    if info.state == "EXTENDED":
        return [f"Market is in extended hours ({info.reason}) - entries blocked."]
    return [f"Market is closed ({info.reason}) - entries blocked."]


def _catalyst_dates(label: str, fetch, ticker: str) -> tuple[list, str | None]:
    try:
        return fetch(ticker), None
    except Exception as exc:  # any lookup failure blocks a new entry
        log.warning(
            "order guard: %s calendar unavailable for %s: %s", label, ticker, exc
        )
        return (
            [],
            f"{label.capitalize()} calendar unavailable for {ticker} - entry blocked.",
        )


def enforce_order_guards(
    ticker: str,
    intent: OrderIntent,
    settings: Settings,
    clock: MarketClock,
) -> GuardResult:
    """Run every dispatch guard for one order. EXIT is always allowed with
    no lookups; ENTRY collects every failing reason, not just the first."""
    if intent == "EXIT":
        return GuardResult(allowed=True)

    now = pd.Timestamp.now(tz="America/New_York")
    reasons = _session_reasons(now, settings, clock)
    earnings, earnings_error = _catalyst_dates("earnings", fetch_earnings_dates, ticker)
    splits, splits_error = _catalyst_dates("split", fetch_stock_splits, ticker)
    reasons += [r for r in (earnings_error, splits_error) if r]

    guard = EarningsLockoutGuard(lockout_hours=settings.earnings_lockout_hours)
    blocked, reason = guard.is_blocked(now, earnings, splits)
    if blocked and reason:
        reasons.append(reason)
    return GuardResult(allowed=not reasons, reasons=reasons)
