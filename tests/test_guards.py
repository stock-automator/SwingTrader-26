"""Unit tests for `execution.guards` - session clock and earnings/split
lockout, independent of the FastAPI wiring (see `test_execution_api.py`'s
`TestDispatchGuards` for that)."""

from datetime import datetime

import pandas as pd
import pytest

from backend.app.execution.guards import (
    SESSION_AFTER_HOURS,
    SESSION_CLOSED,
    SESSION_PRE_MARKET,
    SESSION_REGULAR,
    EarningsLockoutGuard,
    MarketSessionGuard,
    check_order_guards,
)

# All fixed moments below are a known Wednesday/Saturday in Eastern time.
_WEDNESDAY = datetime(2026, 9, 16)
_SATURDAY = datetime(2026, 9, 19)


class TestMarketSessionGuard:
    def test_regular_session(self):
        guard = MarketSessionGuard()
        assert guard.current_session(_WEDNESDAY.replace(hour=10)) == SESSION_REGULAR

    def test_pre_market(self):
        guard = MarketSessionGuard()
        assert guard.current_session(_WEDNESDAY.replace(hour=5)) == SESSION_PRE_MARKET

    def test_after_hours(self):
        guard = MarketSessionGuard()
        assert guard.current_session(_WEDNESDAY.replace(hour=17)) == SESSION_AFTER_HOURS

    def test_overnight_is_closed(self):
        guard = MarketSessionGuard()
        assert guard.current_session(_WEDNESDAY.replace(hour=1)) == SESSION_CLOSED

    def test_weekend_is_closed(self):
        guard = MarketSessionGuard()
        assert guard.current_session(_SATURDAY.replace(hour=10)) == SESSION_CLOSED

    def test_holiday_is_closed(self):
        holiday = pd.Timestamp(_WEDNESDAY.date())
        guard = MarketSessionGuard(holidays=frozenset({holiday}))
        assert guard.current_session(_WEDNESDAY.replace(hour=10)) == SESSION_CLOSED

    def test_regular_session_allows_dispatch(self):
        allowed, reason = MarketSessionGuard().allows_dispatch(
            _WEDNESDAY.replace(hour=10)
        )
        assert allowed is True
        assert reason is None

    def test_closed_blocks_dispatch(self):
        allowed, reason = MarketSessionGuard().allows_dispatch(_SATURDAY)
        assert allowed is False
        assert "closed" in reason.lower()

    def test_extended_hours_blocked_by_default(self):
        allowed, reason = MarketSessionGuard().allows_dispatch(
            _WEDNESDAY.replace(hour=5)
        )
        assert allowed is False
        assert "extended-hours" in reason.lower() or "pre_market" in reason.lower()

    def test_extended_hours_allowed_when_opted_in(self):
        allowed, reason = MarketSessionGuard(allow_extended_hours=True).allows_dispatch(
            _WEDNESDAY.replace(hour=5)
        )
        assert allowed is True
        assert reason is not None  # still flagged as illiquid, just not blocked


class TestEarningsLockoutGuard:
    def test_rejects_negative_lockout_hours(self):
        with pytest.raises(ValueError):
            EarningsLockoutGuard(lockout_hours=-1)

    def test_no_calendar_never_blocks(self):
        guard = EarningsLockoutGuard()
        blocked, reason = guard.is_blocked(pd.Timestamp("2026-09-16"), None)
        assert blocked is False
        assert reason is None

    def test_far_from_any_earnings_not_blocked(self):
        guard = EarningsLockoutGuard(lockout_hours=48)
        blocked, _ = guard.is_blocked(
            pd.Timestamp("2026-09-16"), [pd.Timestamp("2026-01-01")]
        )
        assert blocked is False

    def test_within_window_before_earnings_blocks(self):
        guard = EarningsLockoutGuard(lockout_hours=48)
        blocked, reason = guard.is_blocked(
            pd.Timestamp("2026-09-16 10:00"), [pd.Timestamp("2026-09-17")]
        )
        assert blocked is True
        assert "earnings" in reason.lower()

    def test_within_window_after_earnings_blocks(self):
        guard = EarningsLockoutGuard(lockout_hours=48)
        blocked, _ = guard.is_blocked(
            pd.Timestamp("2026-09-18 10:00"), [pd.Timestamp("2026-09-17")]
        )
        assert blocked is True

    def test_split_date_also_blocks(self):
        guard = EarningsLockoutGuard(lockout_hours=48)
        blocked, reason = guard.is_blocked(
            pd.Timestamp("2026-09-16 10:00"),
            earnings_dates=None,
            split_dates=[pd.Timestamp("2026-09-17")],
        )
        assert blocked is True
        assert "split" in reason.lower()

    def test_tz_aware_as_of_does_not_raise(self):
        guard = EarningsLockoutGuard(lockout_hours=48)
        as_of = pd.Timestamp("2026-09-16 10:00", tz="America/New_York")
        blocked, _ = guard.is_blocked(as_of, [pd.Timestamp("2026-09-17")])
        assert blocked is True


class TestCheckOrderGuards:
    def test_both_gates_pass(self):
        result = check_order_guards(
            pd.Timestamp("2026-09-16 10:00"),
            earnings_dates=[pd.Timestamp("2026-01-01")],
        )
        assert result.allowed is True
        assert result.reasons == []

    def test_session_and_earnings_reasons_both_reported(self):
        result = check_order_guards(
            pd.Timestamp("2026-09-19 10:00"),  # Saturday
            earnings_dates=[pd.Timestamp("2026-09-19")],
        )
        assert result.allowed is False
        assert len(result.reasons) == 2
