"""
Tests for `backend.app.api.deps`: concurrent ticker loading, retry/backoff,
and watchlist truncation reporting.

Covers the scanner-bottleneck fix - a several-hundred-name watchlist used to
be capped at 60 tickers and loaded one at a time, which is what made a full
scan look like it was silently skipping symbols.
"""

import threading
import time

import pandas as pd
import pytest

import backend.app.api.deps as deps_module
from backend.app.config import Settings
from backend.app.data.loader import DataUnavailableError


def _bars(n: int = 10) -> pd.DataFrame:
    index = pd.date_range("2023-01-02", periods=n, freq="B")
    close = pd.Series([100.0 + i for i in range(n)], index=index)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": pd.Series([1_000_000] * n, index=index),
        },
        index=index,
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, watchlist_path=tmp_path / "watchlist.txt")


class TestLoadFrames:
    def test_loads_successful_tickers_and_warns_on_failures(
        self, monkeypatch, settings
    ):
        def fake_load_prices(
            ticker, start=None, end=None, data_dir=None, allow_download=True
        ):
            if ticker == "BAD":
                raise DataUnavailableError(f"{ticker} not in test universe")
            return _bars()

        monkeypatch.setattr(deps_module, "load_prices", fake_load_prices)

        frames, warnings = deps_module.load_frames(["AAPL", "BAD", "MSFT"], settings)

        assert set(frames) == {"AAPL", "MSFT"}
        assert len(warnings) == 1
        assert "BAD" in warnings[0]

    def test_empty_ticker_list_returns_empty_results(self, settings):
        frames, warnings = deps_module.load_frames([], settings)
        assert frames == {}
        assert warnings == []

    def test_retries_transient_failure_then_succeeds(self, monkeypatch, settings):
        attempts: dict[str, int] = {}

        def flaky_load_prices(
            ticker, start=None, end=None, data_dir=None, allow_download=True
        ):
            attempts[ticker] = attempts.get(ticker, 0) + 1
            if attempts[ticker] < 2:
                raise DataUnavailableError(f"{ticker}: transient rate limit")
            return _bars()

        monkeypatch.setattr(deps_module, "load_prices", flaky_load_prices)
        monkeypatch.setattr(deps_module, "RETRY_BACKOFF_SECONDS", 0.0)

        frames, warnings = deps_module.load_frames(["AAPL"], settings)

        assert "AAPL" in frames
        assert warnings == []
        assert attempts["AAPL"] == 2

    def test_unexpected_exception_from_one_ticker_does_not_crash_the_batch(
        self, monkeypatch, settings
    ):
        """Regression: a delisted/malformed ticker can raise something other
        than `DataUnavailableError` (e.g. a `ValueError` deep inside pandas
        column selection). `_load_one` must catch it and report a per-ticker
        error tuple, not let it propagate out of `future.result()` and abort
        every other ticker in the batch."""

        def flaky_load_prices(
            ticker, start=None, end=None, data_dir=None, allow_download=True
        ):
            if ticker == "BK":
                raise ValueError("Columns must be same length as key")
            return _bars()

        monkeypatch.setattr(deps_module, "load_prices", flaky_load_prices)

        frames, warnings = deps_module.load_frames(["AAPL", "BK", "MSFT"], settings)

        assert set(frames) == {"AAPL", "MSFT"}
        assert len(warnings) == 1
        assert "Columns must be same length as key" in warnings[0]

    def test_gives_up_after_max_attempts(self, monkeypatch, settings):
        attempts: dict[str, int] = {}

        def always_fails(
            ticker, start=None, end=None, data_dir=None, allow_download=True
        ):
            attempts[ticker] = attempts.get(ticker, 0) + 1
            raise DataUnavailableError(f"{ticker}: down")

        monkeypatch.setattr(deps_module, "load_prices", always_fails)
        monkeypatch.setattr(deps_module, "RETRY_BACKOFF_SECONDS", 0.0)

        frames, warnings = deps_module.load_frames(["AAPL"], settings)

        assert frames == {}
        assert len(warnings) == 1
        assert attempts["AAPL"] == deps_module.RETRY_ATTEMPTS

    def test_does_not_retry_when_downloads_disabled(self, monkeypatch, settings):
        settings = Settings(
            data_dir=settings.data_dir,
            watchlist_path=settings.watchlist_path,
            allow_downloads=False,
        )
        attempts: dict[str, int] = {}

        def always_fails(
            ticker, start=None, end=None, data_dir=None, allow_download=True
        ):
            attempts[ticker] = attempts.get(ticker, 0) + 1
            raise DataUnavailableError(f"{ticker}: not cached")

        monkeypatch.setattr(deps_module, "load_prices", always_fails)

        frames, warnings = deps_module.load_frames(["AAPL"], settings)

        assert frames == {}
        assert attempts["AAPL"] == 1

    def test_large_universe_is_loaded_concurrently_within_worker_bound(
        self, monkeypatch, settings
    ):
        """512-ish tickers must all resolve (the historical bug truncated
        silently well before this), and concurrency must never exceed
        `screener_max_workers`."""
        settings = Settings(
            data_dir=settings.data_dir,
            watchlist_path=settings.watchlist_path,
            screener_max_workers=8,
        )
        tickers = [f"T{i:04d}" for i in range(200)]

        lock = threading.Lock()
        in_flight = 0
        max_in_flight = 0

        def slow_load_prices(
            ticker, start=None, end=None, data_dir=None, allow_download=True
        ):
            nonlocal in_flight, max_in_flight
            with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            time.sleep(0.01)
            with lock:
                in_flight -= 1
            return _bars()

        monkeypatch.setattr(deps_module, "load_prices", slow_load_prices)

        frames, warnings = deps_module.load_frames(tickers, settings)

        assert len(frames) == len(tickers)
        assert warnings == []
        assert max_in_flight <= settings.screener_max_workers


class TestLoadWatchlist:
    def test_missing_file_returns_empty_list(self, settings):
        assert deps_module.load_watchlist(settings) == []
        assert deps_module.watchlist_overflow(settings) == 0

    def test_reads_tickers_ignoring_blank_lines_and_comments(self, settings):
        settings.watchlist_path.write_text("AAPL\n\n# a comment\nmsft\n")
        assert deps_module.load_watchlist(settings) == ["AAPL", "MSFT"]

    def test_caps_at_screener_max_tickers_and_reports_overflow(self, settings):
        lines = "\n".join(f"T{i:04d}" for i in range(50))
        settings.watchlist_path.write_text(lines)
        capped_settings = Settings(
            data_dir=settings.data_dir,
            watchlist_path=settings.watchlist_path,
            screener_max_tickers=10,
        )

        tickers = deps_module.load_watchlist(capped_settings)
        overflow = deps_module.watchlist_overflow(capped_settings)

        assert len(tickers) == 10
        assert overflow == 40

    def test_no_overflow_when_watchlist_fits_under_cap(self, settings):
        settings.watchlist_path.write_text("AAPL\nMSFT\n")
        assert deps_module.watchlist_overflow(settings) == 0
