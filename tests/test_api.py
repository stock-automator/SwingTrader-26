"""
Tests for the FastAPI application: `backend/app/main.py` and `api/*.py`.

Price loading is monkeypatched to synthetic OHLCV data so the suite never
touches the network or the (large, git-ignored) parquet cache - `ALLOW_DOWNLOADS`
being off in CI is a defense-in-depth backstop, not what makes these tests
hermetic.
"""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
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
def fake_prices(monkeypatch):
    """Patch every module-local `load_prices` name to serve synthetic bars
    for a small fixed universe, and fail (like an uncached ticker would)
    for anything else."""
    universe = {
        "AAPL": _trending_ohlcv(seed=1),
        "MSFT": _trending_ohlcv(seed=2, drift=0.2),
        "SPY": _trending_ohlcv(seed=3, drift=0.15),
    }

    def _load_prices(ticker, start=None, end=None, data_dir=None, allow_download=True):
        ticker = ticker.upper()
        if ticker not in universe:
            raise DataUnavailableError(f"{ticker} not in test universe")
        df = universe[ticker]
        if start is not None:
            df = df[df.index >= pd.Timestamp(start)]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end)]
        return df

    import backend.app.api.backtest as backtest_module
    import backend.app.api.deps as deps_module
    import backend.app.api.screener as screener_module

    monkeypatch.setattr(deps_module, "load_prices", _load_prices)
    monkeypatch.setattr(backtest_module, "load_prices", _load_prices)
    monkeypatch.setattr(screener_module, "load_prices", _load_prices)
    monkeypatch.setattr(
        screener_module, "load_watchlist", lambda settings: ["AAPL", "MSFT"]
    )
    return universe


class TestHealth:
    def test_health_ok(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "finnhub_configured" in body
        assert "allow_downloads" in body


class TestBacktestEndpoint:
    def test_valid_request_returns_full_payload(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest",
            json={
                "strategy": "moving_average_cross",
                "tickers": ["AAPL"],
                "initial_capital": 1000,
            },
        )
        assert response.status_code == 200
        body = response.json()

        assert body["initial_capital"] == 1000.0
        assert body["tickers"] == ["AAPL"]
        assert "$1,000" in body["headline"]
        assert body["summaries"]["strategy"]["initial_value"] == 1000.0
        assert body["vs_spy"] is not None
        assert isinstance(body["equity_curves"], list) and body["equity_curves"]
        assert isinstance(body["trades"], list)

    def test_default_initial_capital_is_one_thousand(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest",
            json={"strategy": "donchian_breakout", "tickers": ["AAPL"]},
        )
        assert response.status_code == 200
        assert response.json()["initial_capital"] == 1000.0

    def test_unknown_strategy_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest",
            json={"strategy": "not_a_real_strategy", "tickers": ["AAPL"]},
        )
        assert response.status_code == 422

    def test_empty_tickers_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest", json={"strategy": "donchian_breakout", "tickers": []}
        )
        assert response.status_code == 422

    def test_non_positive_initial_capital_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest",
            json={
                "strategy": "donchian_breakout",
                "tickers": ["AAPL"],
                "initial_capital": 0,
            },
        )
        assert response.status_code == 422

    def test_unavailable_ticker_is_503(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest",
            json={"strategy": "donchian_breakout", "tickers": ["NOPE_NOT_CACHED"]},
        )
        assert response.status_code == 503

    def test_missing_spy_still_returns_200_with_warning(self, client, monkeypatch):
        universe = {"AAPL": _trending_ohlcv(seed=1)}

        def _load_prices(
            ticker, start=None, end=None, data_dir=None, allow_download=True
        ):
            ticker = ticker.upper()
            if ticker not in universe:
                raise DataUnavailableError(f"{ticker} unavailable")
            return universe[ticker]

        import backend.app.api.backtest as backtest_module
        import backend.app.api.deps as deps_module

        monkeypatch.setattr(deps_module, "load_prices", _load_prices)
        monkeypatch.setattr(backtest_module, "load_prices", _load_prices)

        response = client.post(
            "/api/v1/backtest",
            json={"strategy": "donchian_breakout", "tickers": ["AAPL"]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["vs_spy"] is None
        assert any("SPY" in w for w in body["warnings"])


class TestScreenerLiveEndpoint:
    def test_scans_requested_tickers(self, client, fake_prices):
        response = client.get(
            "/api/v1/screener/live",
            params={"strategy": "donchian_breakout", "tickers": "AAPL,MSFT"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["scanned"] == 2
        assert "setups" in body and "skip_reasons" in body

    def test_defaults_to_watchlist(self, client, fake_prices):
        response = client.get("/api/v1/screener/live")
        assert response.status_code == 200
        assert response.json()["scanned"] == 2  # patched load_watchlist -> [AAPL, MSFT]

    def test_unknown_strategy_is_422(self, client, fake_prices):
        response = client.get(
            "/api/v1/screener/live", params={"strategy": "not_a_real_strategy"}
        )
        assert response.status_code == 422

    def test_setups_are_well_formed(self, client, fake_prices):
        response = client.get(
            "/api/v1/screener/live",
            params={"strategy": "moving_average_cross", "tickers": "AAPL,MSFT"},
        )
        body = response.json()
        for setup in body["setups"]:
            assert setup["direction"] in {"LONG", "SHORT", "EXIT_LONG", "FLAT"}
            assert setup["ticker"] in {"AAPL", "MSFT"}


class TestScreenerWebSocket:
    def test_pushes_a_scan_frame_on_connect(self, client, fake_prices, monkeypatch):
        from backend.app.config import get_settings

        fast_settings = Settings(
            allow_downloads=True,
            watchlist_path=get_settings().watchlist_path,
            ws_poll_seconds=0.05,
        )
        import backend.app.api.screener as screener_module

        monkeypatch.setattr(screener_module, "get_settings", lambda: fast_settings)

        with client.websocket_connect(
            "/ws/screener?strategy=donchian_breakout"
        ) as websocket:
            frame = websocket.receive_json()
            assert "setups" in frame
            assert frame["scanned"] == 2
