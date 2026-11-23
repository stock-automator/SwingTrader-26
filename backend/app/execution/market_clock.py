"""
US equity market session state from Alpaca's real trading calendar.

Holidays, weekends and half-day early closes all come from
`TradingClient.get_calendar`: a date missing from the calendar is a closed
day, and `Calendar.close` already carries a half-day's early close. Alpaca
returns `open`/`close` as naive New York wall-clock times, so they are
localized to `America/New_York` here, never assumed to be UTC.

Errors from Alpaca propagate - the caller (`execution.guards`) decides that
a calendar it cannot read blocks new entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient
from alpaca.trading.models import Calendar
from alpaca.trading.requests import GetCalendarRequest
from fastapi import Depends

from ..config import Settings, get_settings

NY = ZoneInfo("America/New_York")

SessionState = Literal["REGULAR", "EXTENDED", "CLOSED"]


@dataclass(frozen=True)
class SessionInfo:
    """Session state at one moment. `open`/`close` are the regular-session
    bounds (New York, tz-aware), or `None` on a non-trading day."""

    state: SessionState
    open: datetime | None
    close: datetime | None
    reason: str


def _hhmm(day: date, value: str | None) -> datetime | None:
    if not value:
        return None
    digits = value.replace(":", "")
    return datetime.combine(day, time(int(digits[:2]), int(digits[2:4])), NY)


def session_for(moment: datetime, calendar_day: Calendar | None) -> SessionInfo:
    """Session state at `moment` (tz-aware) given that New York day's Alpaca
    calendar entry, or `None` if the market does not trade that day."""
    now = moment.astimezone(NY)
    if calendar_day is None:
        return SessionInfo("CLOSED", None, None, f"{now.date()} is not a trading day")

    open_ = calendar_day.open.replace(tzinfo=NY)
    close = calendar_day.close.replace(tzinfo=NY)
    if open_ <= now < close:
        return SessionInfo("REGULAR", open_, close, "regular session")

    session_open = _hhmm(calendar_day.date, calendar_day.session_open)
    session_close = _hhmm(calendar_day.date, calendar_day.session_close)
    if session_open and session_close and session_open <= now < session_close:
        return SessionInfo("EXTENDED", open_, close, "extended-hours session")
    return SessionInfo(
        "CLOSED",
        open_,
        close,
        f"outside regular hours ({open_:%H:%M}-{close:%H:%M} ET)",
    )


class MarketClock:
    """Session state from Alpaca's calendar, fetched once per New York day."""

    def __init__(self, client: TradingClient | None):
        self._client = client
        self._cache: dict[date, Calendar | None] = {}

    def _calendar_day(self, day: date) -> Calendar | None:
        if day not in self._cache:
            if self._client is None:
                raise RuntimeError(
                    "Alpaca is not configured - market calendar unavailable"
                )
            days = self._client.get_calendar(GetCalendarRequest(start=day, end=day))
            self._cache[day] = next((d for d in days if d.date == day), None)
        return self._cache[day]

    def session(self, now: datetime | None = None) -> SessionInfo:
        moment = (now or datetime.now(NY)).astimezone(NY)
        return session_for(moment, self._calendar_day(moment.date()))


@lru_cache(maxsize=4)
def _clock_for(api_key: str | None, api_secret: str | None) -> MarketClock:
    if not (api_key and api_secret):
        return MarketClock(None)
    return MarketClock(TradingClient(api_key, api_secret, paper=True))


def get_market_clock(settings: Settings = Depends(get_settings)) -> MarketClock:
    """FastAPI dependency. Without Alpaca keys the clock raises on every
    lookup, so new entries fail closed without touching the network."""
    return _clock_for(settings.alpaca_api_key, settings.alpaca_api_secret)
