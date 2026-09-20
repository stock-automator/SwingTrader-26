"""
Tests for `GET /api/v1/market/regime`. SPY/QQQ/VIX/breadth resolution is
monkeypatched at the `backend.app.api.market` module level so nothing here
touches the network or a real parquet cache - `MarketRegimeEngine`'s own
classification logic is covered separately in `test_market_regime_engine.py`.
"""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backend.app.api.market as market_module
from backend.app.config import Settings, get_settings
from backend.app.data.loader import DataUnavailableError
from backend.app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    app.dependency_overrides[get_settings] = lambda: Settings(allow_downloads=False)
    market_module.reset_regime_cache_for_tests()
    yield
    app.dependency_overrides.pop(get_settings, None)
    market_module.reset_regime_cache_for_tests()


def _trending_frame(start: float, drift: float, n: int = 260) -> pd.DataFrame:
    close = start + drift * np.arange(n)
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({"Close": close}, index=idx)


class TestMarketRegimeEndpoint:
    def test_healthy_bull_inputs_return_bull_confirmed(self, monkeypatch, client):
        def fake_load_prices(ticker, **kw):
            if ticker in ("SPY", "QQQ"):
                return _trending_frame(100, 1.0)
            if ticker == "^VIX":
                return _trending_frame(13.0, 0.0)
            raise DataUnavailableError(ticker)

        monkeypatch.setattr(market_module, "load_prices", fake_load_prices)
        monkeypatch.setattr(
            market_module, "cached_tickers", lambda data_dir: ["A", "B"]
        )
        monkeypatch.setattr(
            market_module,
            "load_frames",
            lambda tickers, settings: (
                {t: _trending_frame(50, 0.3) for t in tickers},
                [],
            ),
        )

        response = client.get("/api/v1/market/regime")

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "BULL_CONFIRMED"
        assert body["spy_alignment"] == "BULLISH"
        assert body["breadth_total"] == 2

    def test_missing_spy_qqq_vix_degrades_gracefully(self, monkeypatch, client):
        def _always_unavailable(ticker, **kw):
            raise DataUnavailableError(f"{ticker} not available")

        monkeypatch.setattr(market_module, "load_prices", _always_unavailable)
        monkeypatch.setattr(market_module, "cached_tickers", lambda data_dir: [])
        monkeypatch.setattr(
            market_module, "load_frames", lambda tickers, settings: ({}, [])
        )

        response = client.get("/api/v1/market/regime")

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "CAUTION_CHOP"
        assert body["spy_alignment"] == "UNKNOWN"
        assert body["vix_level"] is None
        assert body["breadth_pct"] is None

    def test_bearish_breadth_returns_bear_defensive(self, monkeypatch, client):
        monkeypatch.setattr(
            market_module,
            "load_prices",
            lambda ticker, **kw: (
                _trending_frame(500, -1.0) if ticker in ("SPY", "QQQ") else None
            ),
        )
        monkeypatch.setattr(
            market_module, "cached_tickers", lambda data_dir: ["A", "B", "C"]
        )
        monkeypatch.setattr(
            market_module,
            "load_frames",
            lambda tickers, settings: (
                {t: _trending_frame(500, -1.0) for t in tickers},
                [],
            ),
        )

        response = client.get("/api/v1/market/regime")

        assert response.status_code == 200
        assert response.json()["state"] == "BEAR_DEFENSIVE"
