"""Tests for `GET /api/v1/signals/live-today`."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.data.loader import DataUnavailableError
from backend.app.main import app


def _trending_ohlcv(n: int = 300, seed: int = 7, drift: float = 0.3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2022-01-03", periods=n, freq="B")
    noise = rng.normal(0, 1.0, n).cumsum()
    close = pd.Series(100.0 + np.arange(n) * drift + noise, index=index).clip(lower=5.0)
    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": pd.Series(1_000_000.0, index=index),
        },
        index=index,
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def fake_universe(monkeypatch):
    universe = {
        "AAPL": _trending_ohlcv(seed=1),
        "MSFT": _trending_ohlcv(seed=2, drift=0.2),
        "SPY": _trending_ohlcv(seed=3, drift=0.15),
    }

    def _load_prices(ticker, start=None, end=None, data_dir=None, allow_download=True):
        ticker = ticker.upper()
        if ticker not in universe:
            raise DataUnavailableError(f"{ticker} not in test universe")
        return universe[ticker]

    import backend.app.api.deps as deps_module
    import backend.app.api.signals as signals_module

    monkeypatch.setattr(deps_module, "load_prices", _load_prices)
    monkeypatch.setattr(signals_module, "load_prices", _load_prices)
    monkeypatch.setattr(
        signals_module, "load_watchlist", lambda settings: ["AAPL", "MSFT"]
    )
    return universe


class TestLiveToday:
    def test_returns_rows_across_multiple_strategies(self, client, fake_universe):
        response = client.get(
            "/api/v1/signals/live-today",
            params={"strategies": "donchian_breakout,moving_average_cross"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "rows" in body and "generated_at" in body
        for row in body["rows"]:
            assert row["direction"] in {"LONG", "SHORT"}
            assert row["strategy"] in {"donchian_breakout", "moving_average_cross"}
            if row["direction"] == "SHORT":
                # Screening-only: the backtest engine is long-only, so there
                # is nothing to estimate a win probability from.
                assert row["win_probability"] is None
            else:
                wp = row["win_probability"]
                assert wp is None or 0.0 <= wp <= 1.0
                assert row["win_probability_method"] in {
                    "regime_matched_backtest",
                    "block_bootstrap",
                    "insufficient_data",
                }

    def test_long_row_gets_a_real_win_probability_with_enough_history(
        self, client, monkeypatch
    ):
        """With enough bars for the strategy to have closed plenty of
        historical trades, a LONG row's win_probability must be a real
        number, not the old null placeholder."""
        # This particular seed/drift is a known LONG-at-last-bar case for
        # kama_trend at 1500 bars - other strategies/seeds mostly end FLAT
        # on synthetic noise, which would make this test flaky.
        long_history = {"AAPL": _trending_ohlcv(n=1500, seed=16, drift=0.3)}

        def _load_prices(
            ticker, start=None, end=None, data_dir=None, allow_download=True
        ):
            ticker = ticker.upper()
            if ticker not in long_history:
                raise DataUnavailableError(f"{ticker} not in test universe")
            return long_history[ticker]

        import backend.app.api.deps as deps_module
        import backend.app.api.signals as signals_module

        monkeypatch.setattr(deps_module, "load_prices", _load_prices)
        monkeypatch.setattr(signals_module, "load_prices", _load_prices)
        monkeypatch.setattr(signals_module, "load_watchlist", lambda settings: ["AAPL"])

        response = client.get(
            "/api/v1/signals/live-today",
            params={"strategies": "kama_trend"},
        )
        assert response.status_code == 200
        long_rows = [r for r in response.json()["rows"] if r["direction"] == "LONG"]
        assert long_rows, "expected at least one LONG row from a 1500-bar trend"
        for row in long_rows:
            assert row["win_probability_sample_size"] is not None
            if row["win_probability_method"] != "insufficient_data":
                assert row["win_probability"] is not None

    def test_unknown_strategy_is_422(self, client, fake_universe):
        response = client.get(
            "/api/v1/signals/live-today", params={"strategies": "not_a_real_strategy"}
        )
        assert response.status_code == 422

    def test_benchmark_dependent_strategy_included_when_spy_available(
        self, client, fake_universe
    ):
        response = client.get(
            "/api/v1/signals/live-today", params={"strategies": "relative_strength"}
        )
        assert response.status_code == 200

    def test_benchmark_dependent_strategy_skips_gracefully_without_spy(
        self, client, monkeypatch
    ):
        universe = {"AAPL": _trending_ohlcv(seed=1)}

        def _load_prices(
            ticker, start=None, end=None, data_dir=None, allow_download=True
        ):
            if ticker.upper() not in universe:
                raise DataUnavailableError(f"{ticker} unavailable")
            return universe[ticker.upper()]

        import backend.app.api.deps as deps_module
        import backend.app.api.signals as signals_module

        monkeypatch.setattr(deps_module, "load_prices", _load_prices)
        monkeypatch.setattr(signals_module, "load_prices", _load_prices)
        monkeypatch.setattr(signals_module, "load_watchlist", lambda settings: ["AAPL"])

        response = client.get(
            "/api/v1/signals/live-today", params={"strategies": "relative_strength"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["rows"] == []
        assert any("relative_strength" in w for w in body["warnings"])

    def test_explicit_tickers_override_watchlist(self, client, fake_universe):
        response = client.get(
            "/api/v1/signals/live-today",
            params={"strategies": "donchian_breakout", "tickers": "AAPL"},
        )
        assert response.status_code == 200
        for row in response.json()["rows"]:
            assert row["ticker"] == "AAPL"

    def test_empty_watchlist_returns_empty_grid(self, client, monkeypatch):
        import backend.app.api.signals as signals_module

        monkeypatch.setattr(signals_module, "load_watchlist", lambda settings: [])
        response = client.get("/api/v1/signals/live-today")
        assert response.status_code == 200
        assert response.json()["rows"] == []
