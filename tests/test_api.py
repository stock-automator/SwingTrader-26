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

    import backend.app.api.analytics as analytics_module
    import backend.app.api.backtest as backtest_module
    import backend.app.api.deps as deps_module
    import backend.app.api.screener as screener_module

    monkeypatch.setattr(deps_module, "load_prices", _load_prices)
    monkeypatch.setattr(backtest_module, "load_prices", _load_prices)
    monkeypatch.setattr(screener_module, "load_prices", _load_prices)
    monkeypatch.setattr(analytics_module, "load_prices", _load_prices)
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


class TestDataSyncEndpoint:
    @pytest.fixture(autouse=True)
    def _reset_job_state(self):
        import backend.app.api.data_sync as data_sync_module

        data_sync_module._job_state = data_sync_module._SyncJobState()
        yield
        data_sync_module._job_state = data_sync_module._SyncJobState()

    def test_sync_explicit_tickers_runs_in_background_and_reports_status(
        self, client, monkeypatch
    ):
        import backend.app.api.data_sync as data_sync_module

        class _FakeManager:
            def __init__(self, data_dir):
                self.data_dir = data_dir

            def sync_ticker(self, ticker, now=None):
                from backend.app.quant.data.parquet_manager import SyncResult

                return SyncResult(ticker=ticker, status="synced", rows_added=3)

        monkeypatch.setattr(data_sync_module, "ParquetSyncManager", _FakeManager)

        response = client.post("/api/v1/data/sync", json={"tickers": ["aapl", "msft"]})
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "started"
        assert body["tickers"] == ["AAPL", "MSFT"]

        status = client.get("/api/v1/data/sync/status").json()
        assert status["in_progress"] is False
        assert status["started_at"] is not None
        results = {r["ticker"]: r for r in status["results"]}
        assert results["AAPL"]["status"] == "synced"
        assert results["AAPL"]["rows_added"] == 3
        assert results["MSFT"]["status"] == "synced"

    def test_omitted_tickers_falls_back_to_watchlist(self, client, monkeypatch):
        import backend.app.api.data_sync as data_sync_module

        monkeypatch.setattr(
            data_sync_module, "load_watchlist", lambda settings: ["AAPL", "MSFT"]
        )

        class _FakeManager:
            def __init__(self, data_dir):
                pass

            def sync_ticker(self, ticker, now=None):
                from backend.app.quant.data.parquet_manager import SyncResult

                return SyncResult(ticker=ticker, status="up_to_date")

        monkeypatch.setattr(data_sync_module, "ParquetSyncManager", _FakeManager)

        response = client.post("/api/v1/data/sync", json={})
        assert response.status_code == 202
        assert response.json()["tickers"] == ["AAPL", "MSFT"]

    def test_empty_watchlist_and_no_tickers_is_422(self, client, monkeypatch):
        import backend.app.api.data_sync as data_sync_module

        monkeypatch.setattr(data_sync_module, "load_watchlist", lambda settings: [])

        response = client.post("/api/v1/data/sync", json={})
        assert response.status_code == 422

    def test_corporate_action_result_surfaces_in_status(self, client, monkeypatch):
        import backend.app.api.data_sync as data_sync_module

        class _FakeManager:
            def __init__(self, data_dir):
                pass

            def sync_ticker(self, ticker, now=None):
                from backend.app.quant.data.parquet_manager import (
                    CorporateActionAdjustment,
                    SyncResult,
                )

                action = CorporateActionAdjustment(
                    anchor_date=pd.Timestamp("2024-06-01"),
                    old_factor=1.0,
                    new_factor=0.5,
                )
                return SyncResult(
                    ticker=ticker,
                    status="corporate_action_adjusted",
                    rows_added=2,
                    corporate_action=action,
                )

        monkeypatch.setattr(data_sync_module, "ParquetSyncManager", _FakeManager)

        client.post("/api/v1/data/sync", json={"tickers": ["AAPL"]})
        status = client.get("/api/v1/data/sync/status").json()

        result = status["results"][0]
        assert result["status"] == "corporate_action_adjusted"
        assert result["corporate_action"]["ratio"] == pytest.approx(0.5)

    def test_status_before_any_sync_is_idle(self, client):
        status = client.get("/api/v1/data/sync/status").json()
        assert status["in_progress"] is False
        assert status["started_at"] is None
        assert status["results"] == []


class TestMonteCarloEndpoint:
    def test_valid_request_returns_full_payload(self, client, fake_prices):
        response = client.post(
            "/api/v1/analytics/monte-carlo",
            json={
                "strategy": "moving_average_cross",
                "strategy_params": {"fast_period": 5, "slow_period": 15, "sl_pct": 0.1},
                "tickers": ["AAPL"],
                "n_simulations": 100,
                "seed": 1,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["n_simulations"] == 100
        assert 0 <= body["risk_of_ruin_pct"] <= 100
        assert "p50" in body["equity_curve_percentiles"]

    def test_unknown_strategy_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/analytics/monte-carlo",
            json={"strategy": "not_a_real_strategy", "tickers": ["AAPL"]},
        )
        assert response.status_code == 422

    def test_empty_tickers_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/analytics/monte-carlo",
            json={"strategy": "donchian_breakout", "tickers": []},
        )
        assert response.status_code == 422

    def test_unavailable_ticker_is_503(self, client, fake_prices):
        response = client.post(
            "/api/v1/analytics/monte-carlo",
            json={"strategy": "donchian_breakout", "tickers": ["NOPE_NOT_CACHED"]},
        )
        assert response.status_code == 503

    def test_strategy_with_no_trades_is_422(self, client, fake_prices):
        # An absurdly long breakout window on a short window of data takes
        # no trades at all - nothing for Monte Carlo to bootstrap.
        response = client.post(
            "/api/v1/analytics/monte-carlo",
            json={
                "strategy": "donchian_breakout",
                "strategy_params": {"breakout_period": 250, "momentum_period": 250},
                "tickers": ["AAPL"],
            },
        )
        assert response.status_code == 422

    def test_relative_strength_strategy_wires_in_the_benchmark(
        self, client, fake_prices
    ):
        response = client.post(
            "/api/v1/analytics/monte-carlo",
            json={
                "strategy": "relative_strength",
                "tickers": ["AAPL"],
                "n_simulations": 50,
            },
        )
        assert response.status_code in (200, 422)  # 422 only if it took no trades


class TestWalkForwardEndpoint:
    def test_valid_request_returns_full_payload(self, client, fake_prices):
        response = client.post(
            "/api/v1/analytics/walk-forward",
            json={
                "strategy": "moving_average_cross",
                "strategy_params": {"fast_period": 5, "slow_period": 15, "sl_pct": 0.1},
                "ticker": "AAPL",
                "is_months": 3,
                "oos_months": 1,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "AAPL"
        assert body["is_window_months"] == 3
        assert isinstance(body["windows"], list)
        assert body["parameter_sensitivity"] is None

    def test_unknown_strategy_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/analytics/walk-forward",
            json={"strategy": "not_a_real_strategy", "ticker": "AAPL"},
        )
        assert response.status_code == 422

    def test_unavailable_ticker_is_503(self, client, fake_prices):
        response = client.post(
            "/api/v1/analytics/walk-forward",
            json={"strategy": "donchian_breakout", "ticker": "NOPE_NOT_CACHED"},
        )
        assert response.status_code == 503

    def test_parameter_sensitivity_sweep_is_included_when_requested(
        self, client, fake_prices
    ):
        response = client.post(
            "/api/v1/analytics/walk-forward",
            json={
                "strategy": "moving_average_cross",
                "strategy_params": {"fast_period": 5, "slow_period": 15, "sl_pct": 0.1},
                "ticker": "AAPL",
                "is_months": 3,
                "oos_months": 1,
                "param_name": "fast_period",
                "param_type": "int",
            },
        )
        assert response.status_code == 200
        sensitivity = response.json()["parameter_sensitivity"]
        assert sensitivity is not None
        assert sensitivity["param_name"] == "fast_period"
        assert len(sensitivity["points"]) == 5
        assert "is_cliff" in sensitivity

    def test_param_name_not_in_strategy_params_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/analytics/walk-forward",
            json={
                "strategy": "moving_average_cross",
                "ticker": "AAPL",
                "param_name": "not_a_real_param",
            },
        )
        assert response.status_code == 422


class TestFactorExposureEndpoint:
    def test_valid_request_returns_full_payload(self, client, fake_prices):
        response = client.post(
            "/api/v1/analytics/factor-exposure",
            json={
                "strategy": "donchian_breakout",
                "tickers": ["AAPL"],
                "risk_free_rate": 0.02,
            },
        )
        assert response.status_code == 200
        body = response.json()
        for key in (
            "alpha_annual_pct",
            "beta",
            "sharpe_ratio",
            "sortino_ratio",
            "calmar_ratio",
            "tail_ratio",
        ):
            assert key in body

    def test_unknown_strategy_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/analytics/factor-exposure",
            json={"strategy": "not_a_real_strategy", "tickers": ["AAPL"]},
        )
        assert response.status_code == 422

    def test_missing_benchmark_is_503(self, client, fake_prices):
        response = client.post(
            "/api/v1/analytics/factor-exposure",
            json={
                "strategy": "donchian_breakout",
                "tickers": ["AAPL"],
                "benchmark": "NOPE_NOT_CACHED",
            },
        )
        assert response.status_code == 503

    def test_unavailable_ticker_is_503(self, client, fake_prices):
        response = client.post(
            "/api/v1/analytics/factor-exposure",
            json={"strategy": "donchian_breakout", "tickers": ["NOPE_NOT_CACHED"]},
        )
        assert response.status_code == 503


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
