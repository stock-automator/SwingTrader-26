"""Tests for `/api/v1/backtest/{historical-date-scan,simulate-trade-execution}`."""

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
def fake_prices(monkeypatch):
    universe = {"AAPL": _trending_ohlcv(seed=1)}

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

    import backend.app.api.deps as deps_module
    import backend.app.api.replay as replay_module

    monkeypatch.setattr(deps_module, "load_prices", _load_prices)
    monkeypatch.setattr(replay_module, "load_prices", _load_prices)
    monkeypatch.setattr(replay_module, "load_watchlist", lambda settings: ["AAPL"])
    return universe


class TestHistoricalDateScan:
    def test_zero_lookahead_target_date_mid_history(self, client, fake_prices):
        target_date = "2022-06-15"
        response = client.post(
            "/api/v1/backtest/historical-date-scan",
            json={
                "strategy": "donchian_breakout",
                "tickers": ["AAPL"],
                "target_date": target_date,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["target_date"] == target_date
        # Every setup's as_of must be on or before target_date - the whole
        # point of this endpoint.
        for setup in body["setups"]:
            assert setup["as_of"] <= target_date

    def test_future_target_date_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest/historical-date-scan",
            json={
                "strategy": "donchian_breakout",
                "tickers": ["AAPL"],
                "target_date": "2999-01-01",
            },
        )
        assert response.status_code == 422

    def test_invalid_target_date_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest/historical-date-scan",
            json={
                "strategy": "donchian_breakout",
                "tickers": ["AAPL"],
                "target_date": "not-a-date",
            },
        )
        assert response.status_code == 422

    def test_target_date_before_any_history_yields_insufficient_history_skip(
        self, client, fake_prices
    ):
        response = client.post(
            "/api/v1/backtest/historical-date-scan",
            json={
                "strategy": "donchian_breakout",
                "tickers": ["AAPL"],
                "target_date": "2022-01-05",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["skip_reasons"].get("INSUFFICIENT_HISTORY") == 1

    def test_unknown_strategy_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest/historical-date-scan",
            json={
                "strategy": "not_a_real_strategy",
                "tickers": ["AAPL"],
                "target_date": "2022-06-15",
            },
        )
        assert response.status_code == 422


class TestSimulateTradeExecution:
    def test_next_open_fills_at_the_following_bars_open(self, client, fake_prices):
        entry_date = "2022-06-15"
        df = fake_prices["AAPL"]
        idx = df.index.get_loc(pd.Timestamp(entry_date))
        expected_open = float(df["Open"].iloc[idx + 1])
        expected_fill_date = df.index[idx + 1].strftime("%Y-%m-%d")

        response = client.post(
            "/api/v1/backtest/simulate-trade-execution",
            json={
                "ticker": "AAPL",
                "entry_date": entry_date,
                "sl_type": "PERCENTAGE",
                "sl_value": 0.05,
                "tp_type": "PERCENTAGE",
                "tp_value": 0.20,
                "execution_mode": "NEXT_OPEN",
                "slippage_pct": 0.0,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["fill_date"] == expected_fill_date
        assert body["reference_price"] == round(expected_open, 4)
        # Zero slippage_pct -> fill price equals the reference price exactly.
        assert body["fill_price"] == round(expected_open, 4)

    def test_same_close_slippage_widens_fill_against_a_long(self, client, fake_prices):
        entry_date = "2022-06-15"
        response = client.post(
            "/api/v1/backtest/simulate-trade-execution",
            json={
                "ticker": "AAPL",
                "entry_date": entry_date,
                "sl_type": "PERCENTAGE",
                "sl_value": 0.05,
                "tp_type": "PERCENTAGE",
                "tp_value": 0.20,
                "execution_mode": "SAME_CLOSE_SLIPPAGE",
                "slippage_pct": 0.01,
                "direction": 1,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["fill_price"] > body["reference_price"]
        assert body["slippage_cost"] > 0

    def test_next_open_on_last_bar_is_422(self, client, fake_prices):
        last_date = fake_prices["AAPL"].index[-1].strftime("%Y-%m-%d")
        response = client.post(
            "/api/v1/backtest/simulate-trade-execution",
            json={
                "ticker": "AAPL",
                "entry_date": last_date,
                "sl_type": "PERCENTAGE",
                "sl_value": 0.05,
                "tp_type": "PERCENTAGE",
                "tp_value": 0.20,
                "execution_mode": "NEXT_OPEN",
            },
        )
        assert response.status_code == 422

    def test_missing_bar_is_404(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest/simulate-trade-execution",
            json={
                "ticker": "AAPL",
                "entry_date": "2022-01-01",  # a Saturday - never a trading bar
                "sl_type": "PERCENTAGE",
                "sl_value": 0.05,
                "tp_type": "PERCENTAGE",
                "tp_value": 0.20,
            },
        )
        assert response.status_code == 404

    def test_unavailable_ticker_is_503(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest/simulate-trade-execution",
            json={
                "ticker": "ZZZZ",
                "entry_date": "2022-06-15",
                "sl_type": "PERCENTAGE",
                "sl_value": 0.05,
                "tp_type": "PERCENTAGE",
                "tp_value": 0.20,
            },
        )
        assert response.status_code == 503

    def test_unknown_execution_mode_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest/simulate-trade-execution",
            json={
                "ticker": "AAPL",
                "entry_date": "2022-06-15",
                "sl_type": "PERCENTAGE",
                "sl_value": 0.05,
                "tp_type": "PERCENTAGE",
                "tp_value": 0.20,
                "execution_mode": "IMMEDIATE",
            },
        )
        assert response.status_code == 422

    def test_reward_risk_below_minimum_is_not_tradable(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest/simulate-trade-execution",
            json={
                "ticker": "AAPL",
                "entry_date": "2022-06-15",
                "sl_type": "PERCENTAGE",
                "sl_value": 0.05,
                "tp_type": "PERCENTAGE",
                "tp_value": 0.06,  # ~1.2R, below the 2.5R risk-engine floor
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["tradable"] is False
        assert body["note"] is not None

    def test_atr_slippage_multiple_reports_dynamic_spread(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest/simulate-trade-execution",
            json={
                "ticker": "AAPL",
                "entry_date": "2022-06-15",
                "sl_type": "PERCENTAGE",
                "sl_value": 0.05,
                "tp_type": "PERCENTAGE",
                "tp_value": 0.20,
                "atr_slippage_multiple": 0.1,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["spread_pct"] > 0
        assert body["slippage_pct_applied"] == pytest.approx(
            body["spread_pct"] + body["market_impact_pct"]
        )

    def test_zero_impact_coefficient_reports_no_market_impact(
        self, client, fake_prices
    ):
        response = client.post(
            "/api/v1/backtest/simulate-trade-execution",
            json={
                "ticker": "AAPL",
                "entry_date": "2022-06-15",
                "sl_type": "PERCENTAGE",
                "sl_value": 0.05,
                "tp_type": "PERCENTAGE",
                "tp_value": 0.20,
                "atr_slippage_multiple": 0.1,
            },
        )
        assert response.status_code == 200
        assert response.json()["market_impact_pct"] == 0.0

    def test_positive_impact_coefficient_widens_fill_and_cost(
        self, client, fake_prices
    ):
        base_payload = {
            "ticker": "AAPL",
            "entry_date": "2022-06-15",
            "sl_type": "PERCENTAGE",
            "sl_value": 0.05,
            "tp_type": "PERCENTAGE",
            "tp_value": 0.20,
            "atr_slippage_multiple": 0.1,
            "account_equity": 1_000_000.0,
        }
        no_impact = client.post(
            "/api/v1/backtest/simulate-trade-execution", json=base_payload
        ).json()
        with_impact = client.post(
            "/api/v1/backtest/simulate-trade-execution",
            json={**base_payload, "impact_coefficient": 0.5},
        ).json()

        assert with_impact["market_impact_pct"] > 0
        assert with_impact["fill_price"] > no_impact["fill_price"]
        assert with_impact["slippage_cost"] > no_impact["slippage_cost"]

    def test_spread_variance_present_and_non_negative(self, client, fake_prices):
        response = client.post(
            "/api/v1/backtest/simulate-trade-execution",
            json={
                "ticker": "AAPL",
                "entry_date": "2022-06-15",
                "sl_type": "PERCENTAGE",
                "sl_value": 0.05,
                "tp_type": "PERCENTAGE",
                "tp_value": 0.20,
                "atr_slippage_multiple": 0.1,
            },
        )
        assert response.status_code == 200
        assert response.json()["spread_variance_pct"] >= 0.0
