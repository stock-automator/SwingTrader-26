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

    def test_relative_strength_strategy_runs_end_to_end(self, client, fake_prices):
        # Needs a benchmark to be wired in via REQUIRES_BENCHMARK - exercises
        # that the API route does so before running the backtest.
        response = client.post(
            "/api/v1/backtest",
            json={"strategy": "relative_strength", "tickers": ["AAPL"]},
        )
        assert response.status_code == 200
        assert response.json()["strategy"] == "Relative Strength Pullback"

    def test_relative_strength_without_benchmark_is_503(self, client, monkeypatch):
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
            json={"strategy": "relative_strength", "tickers": ["AAPL"]},
        )
        assert response.status_code == 503

    def test_regime_gating_toggle_runs_without_error(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest",
            json={
                "strategy": "donchian_breakout",
                "tickers": ["AAPL"],
                "regime_gating": True,
            },
        )
        assert response.status_code == 200

    def test_earnings_blackout_toggle_runs_without_error(
        self, client, fake_prices, monkeypatch
    ):
        import backend.app.api.backtest as backtest_module

        monkeypatch.setattr(
            backtest_module,
            "fetch_earnings_dates",
            lambda ticker, limit=8: [pd.Timestamp("2022-06-15")],
        )

        response = client.post(
            "/api/v1/backtest",
            json={
                "strategy": "donchian_breakout",
                "tickers": ["AAPL"],
                "earnings_blackout": True,
            },
        )
        assert response.status_code == 200

    def test_fee_per_share_and_atr_slippage_are_accepted(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest",
            json={
                "strategy": "donchian_breakout",
                "tickers": ["AAPL"],
                "fee_per_share": 0.005,
                "atr_slippage_multiple": 0.1,
            },
        )
        assert response.status_code == 200
        assert "max_r_multiple" in response.json()["trade_metrics"]

    def test_negative_fee_per_share_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest",
            json={
                "strategy": "donchian_breakout",
                "tickers": ["AAPL"],
                "fee_per_share": -0.01,
            },
        )
        assert response.status_code == 422


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

    def test_response_carries_macro_regime_and_circuit_breaker(
        self, client, fake_prices
    ):
        response = client.get(
            "/api/v1/screener/live",
            params={"strategy": "donchian_breakout", "tickers": "AAPL,MSFT"},
        )
        body = response.json()
        assert body["macro_regime"] in {
            "BULL_TRENDING",
            "BEAR_TRENDING",
            "HIGH_VOLATILITY_CHOP",
            "NEUTRAL",
            "UNKNOWN",
        }
        assert isinstance(body["circuit_breaker_active"], bool)

    def test_bear_trending_spy_suppresses_long_setups(self, client, monkeypatch):
        def _smooth_bear_spy(n: int = 260, seed: int = 99) -> pd.DataFrame:
            # Deterministic, low-noise downtrend - `_trending_ohlcv`'s random
            # walk is noisy enough to read as HIGH_VOLATILITY_CHOP instead,
            # which isn't the case this test is isolating.
            rng = np.random.default_rng(seed)
            index = pd.date_range("2022-01-03", periods=n, freq="B")
            close = pd.Series(
                200 - 0.3 * np.arange(n) + rng.normal(0, 0.05, n), index=index
            )
            return pd.DataFrame(
                {
                    "Open": close,
                    "High": close * 1.002,
                    "Low": close * 0.998,
                    "Close": close,
                    "Volume": 1_000_000.0,
                },
                index=index,
            )

        universe = {
            "AAPL": _trending_ohlcv(seed=1, n=400),
            "SPY": _smooth_bear_spy(),
        }

        def _load_prices(
            ticker, start=None, end=None, data_dir=None, allow_download=True
        ):
            ticker = ticker.upper()
            if ticker not in universe:
                raise DataUnavailableError(f"{ticker} unavailable")
            return universe[ticker]

        import backend.app.api.deps as deps_module
        import backend.app.api.screener as screener_module

        monkeypatch.setattr(deps_module, "load_prices", _load_prices)
        monkeypatch.setattr(screener_module, "load_prices", _load_prices)

        response = client.get(
            "/api/v1/screener/live",
            params={"strategy": "donchian_breakout", "tickers": "AAPL"},
        )
        body = response.json()
        assert body["macro_regime"] == "BEAR_TRENDING"
        for setup in body["setups"]:
            if setup["direction"] == "LONG":
                assert setup["tradable"] is False

    def test_earnings_blackout_query_param_is_accepted(
        self, client, fake_prices, monkeypatch
    ):
        import backend.app.api.screener as screener_module

        monkeypatch.setattr(
            screener_module,
            "fetch_earnings_dates",
            lambda ticker, limit=8: [],
        )
        response = client.get(
            "/api/v1/screener/live",
            params={
                "strategy": "donchian_breakout",
                "tickers": "AAPL",
                "earnings_blackout": True,
            },
        )
        assert response.status_code == 200


class TestOrderTicketEndpoint:
    def test_returns_one_ticket_per_default_tier(self, client):
        response = client.post(
            "/api/v1/order-ticket",
            json={
                "ticker": "aapl",
                "entry_price": 100.0,
                "sl_type": "ATR",
                "sl_value": 2.0,
                "tp_type": "ATR",
                "tp_value": 5.0,
                "atr": 1.0,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["tickets"]) == 3
        assert [t["account_equity"] for t in body["tickets"]] == [
            1000.0,
            5000.0,
            10000.0,
        ]
        assert body["tickets"][0]["ticker"] == "AAPL"

    def test_custom_tiers_are_honored(self, client):
        response = client.post(
            "/api/v1/order-ticket",
            json={
                "ticker": "AAPL",
                "entry_price": 100.0,
                "sl_type": "ATR",
                "sl_value": 2.0,
                "tp_type": "ATR",
                "tp_value": 5.0,
                "atr": 1.0,
                "account_tiers": [2500.0],
            },
        )
        body = response.json()
        assert len(body["tickets"]) == 1
        assert body["tickets"][0]["account_equity"] == 2500.0

    def test_sub_minimum_r_ticket_is_not_tradable(self, client):
        response = client.post(
            "/api/v1/order-ticket",
            json={
                "ticker": "AAPL",
                "entry_price": 100.0,
                "sl_type": "ATR",
                "sl_value": 2.0,
                "tp_type": "ATR",
                "tp_value": 3.0,  # R = 1.5
                "atr": 1.0,
            },
        )
        body = response.json()
        assert all(t["tradable"] is False for t in body["tickets"])

    def test_unknown_level_type_is_422(self, client):
        response = client.post(
            "/api/v1/order-ticket",
            json={
                "ticker": "AAPL",
                "entry_price": 100.0,
                "sl_type": "BOGUS",
                "sl_value": 2.0,
                "tp_type": "ATR",
                "tp_value": 5.0,
                "atr": 1.0,
            },
        )
        assert response.status_code == 422

    def test_invalid_direction_is_422(self, client):
        response = client.post(
            "/api/v1/order-ticket",
            json={
                "ticker": "AAPL",
                "entry_price": 100.0,
                "sl_type": "ATR",
                "sl_value": 2.0,
                "tp_type": "ATR",
                "tp_value": 5.0,
                "atr": 1.0,
                "direction": 0,
            },
        )
        assert response.status_code == 422


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
