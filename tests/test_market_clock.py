"""F1 (batch 02) - `execution.market_clock`: Alpaca-calendar-backed session
state. Every Alpaca call is a fake; nothing touches the network."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from alpaca.trading.models import Calendar
from alpaca.trading.requests import GetCalendarRequest

from backend.app.execution.market_clock import MarketClock, session_for

NY = ZoneInfo("America/New_York")


def _day(d: str, open_: str = "09:30", close: str = "16:00") -> Calendar:
    # Alpaca returns naive open/close datetimes in New York time.
    return Calendar(date=d, open=open_, close=close)


def _ny(*args) -> datetime:
    return datetime(*args, tzinfo=NY)


class _FakeTradingClient:
    def __init__(self, days: dict[date, Calendar] | None = None, raises=None):
        self.days = days or {}
        self.raises = raises
        self.requests: list = []

    def get_calendar(self, filters=None):
        self.requests.append(filters)
        if self.raises is not None:
            raise self.raises
        return [c for d, c in self.days.items() if filters.start <= d <= filters.end]


WEDNESDAY = "2026-09-16"


class TestSessionFor:
    def test_f1_regular_session_mid_day(self):
        info = session_for(_ny(2026, 9, 16, 10, 0), _day(WEDNESDAY))
        assert info.state == "REGULAR"
        assert info.open == _ny(2026, 9, 16, 9, 30)
        assert info.close == _ny(2026, 9, 16, 16, 0)

    def test_f1_exact_open_is_regular(self):
        assert session_for(_ny(2026, 9, 16, 9, 30), _day(WEDNESDAY)).state == "REGULAR"

    def test_f1_exact_close_is_not_regular(self):
        info = session_for(_ny(2026, 9, 16, 16, 0), _day(WEDNESDAY))
        assert info.state != "REGULAR"

    def test_f1_pre_market_is_not_regular(self):
        info = session_for(_ny(2026, 9, 16, 8, 0), _day(WEDNESDAY))
        assert info.state in {"EXTENDED", "CLOSED"}

    def test_f1_after_hours_is_not_regular(self):
        info = session_for(_ny(2026, 9, 16, 17, 0), _day(WEDNESDAY))
        assert info.state in {"EXTENDED", "CLOSED"}

    @pytest.mark.parametrize(
        "moment",
        [
            _ny(2026, 11, 26, 11, 0),  # Thanksgiving (holiday, weekday)
            _ny(2026, 9, 19, 11, 0),  # Saturday
            _ny(2026, 9, 20, 11, 0),  # Sunday
        ],
        ids=["holiday", "saturday", "sunday"],
    )
    def test_f1_no_calendar_day_is_closed(self, moment):
        info = session_for(moment, None)
        assert info.state == "CLOSED"
        assert info.open is None and info.close is None
        assert info.reason

    def test_f1_half_day_before_early_close_is_regular(self):
        half = _day("2026-11-27", close="13:00")  # day after Thanksgiving
        assert session_for(_ny(2026, 11, 27, 12, 30), half).state == "REGULAR"

    def test_f1_half_day_after_early_close_is_closed(self):
        half = _day("2026-11-27", close="13:00")
        info = session_for(_ny(2026, 11, 27, 13, 30), half)
        assert info.state == "CLOSED"
        assert info.close == _ny(2026, 11, 27, 13, 0)

    def test_f1_dst_week_uses_new_york_wall_clock(self):
        # 2026-03-09 is the Monday after US DST starts: 09:30 ET == 13:30 UTC.
        day = _day("2026-03-09")
        before = datetime(2026, 3, 9, 13, 15, tzinfo=timezone.utc)
        after = datetime(2026, 3, 9, 13, 45, tzinfo=timezone.utc)
        assert session_for(before, day).state != "REGULAR"
        assert session_for(after, day).state == "REGULAR"

    def test_f1_utc_input_is_converted_not_assumed(self):
        # 15:00 UTC on a winter day is 10:00 ET -> regular.
        moment = datetime(2026, 12, 2, 15, 0, tzinfo=timezone.utc)
        assert session_for(moment, _day("2026-12-02")).state == "REGULAR"


class TestMarketClock:
    def test_f1_queries_alpaca_calendar_for_the_new_york_day(self):
        client = _FakeTradingClient({date(2026, 9, 16): _day(WEDNESDAY)})
        info = MarketClock(client).session(_ny(2026, 9, 16, 10, 0))
        assert info.state == "REGULAR"
        assert len(client.requests) == 1
        request = client.requests[0]
        assert isinstance(request, GetCalendarRequest)
        assert request.start <= date(2026, 9, 16) <= request.end

    def test_f1_localizes_naive_calendar_times_to_new_york(self):
        client = _FakeTradingClient({date(2026, 9, 16): _day(WEDNESDAY)})
        info = MarketClock(client).session(_ny(2026, 9, 16, 10, 0))
        assert info.open.tzinfo is not None
        assert info.open == _ny(2026, 9, 16, 9, 30)

    def test_f1_calendar_cached_per_day(self):
        client = _FakeTradingClient(
            {
                date(2026, 9, 16): _day(WEDNESDAY),
                date(2026, 9, 17): _day("2026-09-17"),
            }
        )
        clock = MarketClock(client)
        clock.session(_ny(2026, 9, 16, 10, 0))
        clock.session(_ny(2026, 9, 16, 15, 0))
        assert len(client.requests) == 1
        clock.session(_ny(2026, 9, 17, 10, 0))
        assert len(client.requests) == 2

    def test_f1_holiday_from_empty_calendar_is_closed(self):
        client = _FakeTradingClient({})
        info = MarketClock(client).session(_ny(2026, 11, 26, 11, 0))
        assert info.state == "CLOSED"

    def test_f1_utc_late_evening_maps_to_correct_new_york_day(self):
        # 2026-09-17 01:00 UTC is still 2026-09-16 21:00 ET.
        client = _FakeTradingClient({date(2026, 9, 16): _day(WEDNESDAY)})
        MarketClock(client).session(datetime(2026, 9, 17, 1, 0, tzinfo=timezone.utc))
        request = client.requests[0]
        assert request.start <= date(2026, 9, 16) <= request.end

    def test_f1_calendar_error_propagates(self):
        client = _FakeTradingClient(raises=ConnectionError("alpaca down"))
        with pytest.raises(Exception):
            MarketClock(client).session(_ny(2026, 9, 16, 10, 0))


def _plain_day(d: str, open_: str = "09:30", close: str = "16:00"):
    """A calendar day carrying only `date`/`open`/`close` - older alpaca-py
    `Calendar` objects have no `session_open`/`session_close` at all."""
    from types import SimpleNamespace

    day = date.fromisoformat(d)
    return SimpleNamespace(
        date=day,
        open=datetime.combine(day, datetime.strptime(open_, "%H:%M").time()),
        close=datetime.combine(day, datetime.strptime(close, "%H:%M").time()),
    )


class TestPlainCalendarWithoutSessionBounds:
    """Reviewer BLOCK (F1 loop 1): outside regular hours `session_for` read
    `session_open`/`session_close` unconditionally and raised AttributeError
    on a plain calendar day, so the guard blocked entries as "calendar
    unavailable" instead of "closed"."""

    @pytest.mark.parametrize(
        "day, moment",
        [
            ("2026-09-16", _ny(2026, 9, 16, 9, 29)),  # one minute before open
            ("2026-09-16", _ny(2026, 9, 16, 18, 0)),  # after the close
        ],
        ids=["09:29", "18:00"],
    )
    def test_f1_plain_calendar_outside_hours_is_closed(self, day, moment):
        info = session_for(moment, _plain_day(day))
        assert info.state == "CLOSED"

    def test_f1_plain_calendar_half_day_after_early_close_is_closed(self):
        half = _plain_day("2026-11-27", close="13:00")
        info = session_for(_ny(2026, 11, 27, 13, 30), half)
        assert info.state == "CLOSED"
        assert info.close == _ny(2026, 11, 27, 13, 0)

    def test_f1_model_without_session_bounds_is_closed_pre_market(self):
        # The pydantic model with session_open/close left unset (None).
        assert session_for(_ny(2026, 9, 16, 9, 29), _day(WEDNESDAY)).state == "CLOSED"

    @pytest.mark.skipif(
        "session_open" not in Calendar.model_fields,
        reason="this alpaca-py Calendar has no session bounds (older version)",
    )
    def test_f1_session_bounds_present_pre_market_is_extended(self):
        day = Calendar(
            date=WEDNESDAY,
            open="09:30",
            close="16:00",
            session_open="0400",
            session_close="2000",
        )
        assert session_for(_ny(2026, 9, 16, 8, 0), day).state == "EXTENDED"
        assert session_for(_ny(2026, 9, 16, 18, 0), day).state == "EXTENDED"
        assert session_for(_ny(2026, 9, 16, 20, 30), day).state == "CLOSED"
