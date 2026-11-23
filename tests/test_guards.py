"""Unit tests for `execution.guards.EarningsLockoutGuard`, independent of the
FastAPI wiring. Session checks live in `test_market_clock.py` and
`enforce_order_guards` in `test_order_guards_f1.py` (batch 02, F1)."""

import pandas as pd
import pytest

from backend.app.execution.guards import EarningsLockoutGuard


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
