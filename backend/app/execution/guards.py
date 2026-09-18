"""
Order-dispatch safety guards.

Two independent gates, both meant to run immediately before an order is
sent to the broker (`api/execution.py`), not at screening time:

`MarketSessionGuard`
    Refuses to dispatch while the US market is closed, and by default
    refuses illiquid pre-market/after-hours sessions too - a paper fill
    outside regular hours can look fine in a backtest and still be
    unrepresentative of what a live order would actually get filled at.

`EarningsLockoutGuard`
    Refuses to dispatch a new entry within `lockout_hours` (default 48) of
    a scheduled earnings release *or* a stock split, in either direction
    (before or after) - a swing strategy fit on ordinary price action has
    no edge pricing in an event that can gap the stock overnight.

Both guards fail closed on their own clock/calendar math and fail *open* on
missing external data (no earnings/split calendar available for a ticker
does not block the order - see `EarningsLockoutGuard.is_blocked`) - the same
"unscoreable is not blocked" convention `quant.screener.CatalystFilter`
already uses for the screening-time earnings filter this guard complements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

#: Every session boundary below is Eastern - the US market's own clock -
#: regardless of what timezone the server or caller runs in.
_EASTERN = ZoneInfo("America/New_York")

_PRE_MARKET_OPEN = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)
_AFTER_HOURS_CLOSE = time(20, 0)

SESSION_CLOSED = "CLOSED"
SESSION_PRE_MARKET = "PRE_MARKET"
SESSION_REGULAR = "REGULAR"
SESSION_AFTER_HOURS = "AFTER_HOURS"

#: Suppress a new entry within this many hours of a scheduled earnings
#: release or stock split, on either side of it.
DEFAULT_EARNINGS_LOCKOUT_HOURS = 48


def _as_eastern(when: datetime) -> datetime:
    """Normalize `when` to an aware Eastern-time datetime.

    A naive `datetime` is assumed to already be Eastern (the convention
    every caller in this module follows) rather than the host's local zone -
    guessing the host's zone would make the same test non-deterministic
    across machines.
    """
    if when.tzinfo is None:
        return when.replace(tzinfo=_EASTERN)
    return when.astimezone(_EASTERN)


@dataclass(frozen=True)
class MarketSessionGuard:
    """US equity market clock, approximated with weekday + time-of-day.

    Deliberately has no real NYSE holiday calendar wired in (the same
    trade-off `quant.screener.CatalystFilter` makes for trading-day
    counting) - a market holiday that falls on a weekday will read as
    `REGULAR` here. Pass `holidays` with the current year's NYSE closures if
    that gap matters for your deployment.

    Args:
        allow_extended_hours: When `True`, pre-market and after-hours count
            as dispatchable sessions (still flagged in the returned reason
            text as illiquid). `False` (the default) only allows `REGULAR`.
        holidays: Calendar dates (Eastern) the market is fully closed on
            despite being a weekday.
    """

    allow_extended_hours: bool = False
    holidays: frozenset[pd.Timestamp] = field(default_factory=frozenset)

    def current_session(self, now: datetime | None = None) -> str:
        """Which of `SESSION_*` `now` (Eastern, defaults to wall-clock
        `datetime.now()`) falls into."""
        moment = _as_eastern(now or datetime.now(_EASTERN))

        if moment.weekday() >= 5:  # Saturday/Sunday
            return SESSION_CLOSED
        if pd.Timestamp(moment.date()) in self.holidays:
            return SESSION_CLOSED

        clock = moment.time()
        if clock < _PRE_MARKET_OPEN or clock >= _AFTER_HOURS_CLOSE:
            return SESSION_CLOSED
        if clock < _REGULAR_OPEN:
            return SESSION_PRE_MARKET
        if clock < _REGULAR_CLOSE:
            return SESSION_REGULAR
        return SESSION_AFTER_HOURS

    def allows_dispatch(self, now: datetime | None = None) -> tuple[bool, str | None]:
        """Whether an order may be dispatched right now.

        Returns:
            `(True, None)` if dispatch is allowed, else `(False, reason)`.
        """
        session = self.current_session(now)

        if session == SESSION_CLOSED:
            return False, "Market is closed - order dispatch is not allowed."
        if session == SESSION_REGULAR:
            return True, None

        # PRE_MARKET or AFTER_HOURS
        if self.allow_extended_hours:
            return True, (
                f"Dispatching during {session} - liquidity and spreads are "
                "materially worse than regular hours."
            )
        return False, (
            f"Market is in {session} - extended-hours dispatch is disabled "
            "(allow_extended_hours=False)."
        )


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

        `None`/empty calendars are "nothing to check" (fail open), not "no
        catalysts exist" - the caller could not fetch the calendar, which is
        a data-availability gap, not a clean bill of health.
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
    """Combined outcome of every guard `check_order_guards` ran."""

    allowed: bool
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reasons": self.reasons}


def check_order_guards(
    as_of: pd.Timestamp | datetime,
    earnings_dates: list[pd.Timestamp] | None = None,
    split_dates: list[pd.Timestamp] | None = None,
    session_guard: MarketSessionGuard | None = None,
    earnings_guard: EarningsLockoutGuard | None = None,
) -> GuardResult:
    """Run the session guard and the earnings/split lockout guard together.

    Session and catalyst checks are independent - both run regardless of
    whether the other already failed, so a rejected order's `reasons` always
    explains everything wrong with it, not just the first gate hit.
    """
    session_guard = session_guard or MarketSessionGuard()
    earnings_guard = earnings_guard or EarningsLockoutGuard()
    moment = pd.Timestamp(as_of)

    reasons: list[str] = []

    session_ok, session_reason = session_guard.allows_dispatch(moment.to_pydatetime())
    if not session_ok and session_reason:
        reasons.append(session_reason)

    blocked, catalyst_reason = earnings_guard.is_blocked(
        moment, earnings_dates, split_dates
    )
    if blocked and catalyst_reason:
        reasons.append(catalyst_reason)

    return GuardResult(allowed=session_ok and not blocked, reasons=reasons)
