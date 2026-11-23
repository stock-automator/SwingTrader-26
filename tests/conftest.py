"""
Shared pytest configuration.

Puts the repository root on `sys.path` once, so test modules can import
`backend.app.*` directly. Previously every test file prepended the root
itself, which forced its imports below a statement and required a blanket
E402 exemption for `tests/` in `.flake8`.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    """120 bars of synthetic daily OHLCV on a steady uptrend.

    Long enough to clear the 63-bar Donchian momentum warm-up and the 50-bar
    SMA warm-up, so strategies actually emit signals over it.
    """
    index = pd.date_range("2023-01-02", periods=120, freq="B")
    close = pd.Series([100.0 + i * 0.5 for i in range(120)], index=index)

    return pd.DataFrame(
        {
            "Open": close - 0.25,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": pd.Series([1_000_000] * 120, index=index),
        },
        index=index,
    )


class FakeMarketClock:
    """Stand-in for `execution.market_clock.MarketClock` - returns a fixed
    session (or raises) and counts calls, never touching Alpaca."""

    def __init__(self, state: str = "REGULAR", raises: Exception | None = None):
        self.state = state
        self.raises = raises
        self.calls = 0

    def session(self, now=None):
        from backend.app.execution.market_clock import SessionInfo

        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return SessionInfo(
            state=self.state, open=None, close=None, reason=f"fake {self.state}"
        )


@pytest.fixture
def market(monkeypatch):
    """Installs a REGULAR-session fake clock and empty earnings/split
    calendars for every order route. Tests mutate `market.state`/`.raises`
    to simulate closed sessions or calendar outages."""
    import backend.app.execution.guards as guards
    from backend.app.execution.market_clock import get_market_clock
    from backend.app.main import app

    clock = FakeMarketClock()
    app.dependency_overrides[get_market_clock] = lambda: clock
    monkeypatch.setattr(guards, "fetch_earnings_dates", lambda ticker, *a, **k: [])
    monkeypatch.setattr(guards, "fetch_stock_splits", lambda ticker, *a, **k: [])
    yield clock
    app.dependency_overrides.pop(get_market_clock, None)
