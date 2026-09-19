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


class TestBars:
    def test_bars_endpoint_excludes_everything_after_as_of(self, client, fake_prices):
        as_of = "2022-06-15"
        response = client.get(
            "/api/v1/backtest/bars",
            params={"ticker": "AAPL", "as_of": as_of, "lookback_days": 250},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "AAPL"
        assert body["bars"]
        # The whole point-in-time guarantee: nothing past as_of.
        assert all(bar["date"] <= as_of for bar in body["bars"])
        assert body["bars"][-1]["date"] == as_of

    def test_bars_endpoint_respects_lookback_days(self, client, fake_prices):
        response = client.get(
            "/api/v1/backtest/bars",
            params={"ticker": "AAPL", "as_of": "2022-06-15", "lookback_days": 10},
        )
        assert response.status_code == 200
        assert len(response.json()["bars"]) == 10

    def test_bars_endpoint_bar_shape(self, client, fake_prices):
        response = client.get(
            "/api/v1/backtest/bars",
            params={"ticker": "AAPL", "as_of": "2022-06-15", "lookback_days": 5},
        )
        bar = response.json()["bars"][0]
        assert set(bar.keys()) == {"date", "open", "high", "low", "close", "volume"}

    def test_unavailable_ticker_is_503(self, client, fake_prices):
        response = client.get(
            "/api/v1/backtest/bars", params={"ticker": "ZZZZ", "as_of": "2022-06-15"}
        )
        assert response.status_code == 503

    def test_invalid_as_of_is_422(self, client, fake_prices):
        response = client.get(
            "/api/v1/backtest/bars", params={"ticker": "AAPL", "as_of": "not-a-date"}
        )
        assert response.status_code == 422

    def test_as_of_before_any_history_is_404(self, client, fake_prices):
        response = client.get(
            "/api/v1/backtest/bars",
            params={"ticker": "AAPL", "as_of": "2000-01-01"},
        )
        assert response.status_code == 404


def _flat_ohlcv(index: pd.DatetimeIndex) -> dict:
    """Warmup bars: perfectly flat, never touch anything - a fill's
    reference/entry bars in every exit-resolution fixture below."""
    return {
        "Open": [100.0] * len(index),
        "High": [101.0] * len(index),
        "Low": [99.0] * len(index),
        "Close": [100.0] * len(index),
        "Volume": [1_000_000.0] * len(index),
    }


def _exit_resolution_frame(post_fill_bars: list[dict]) -> pd.DataFrame:
    """10 business-day bars: indices 0-4 flat warmup, index 5 the signal bar,
    index 6 the NEXT_OPEN fill bar (opens at 100 - the fill price every
    scenario below is built around), indices 7-9 whatever path
    `post_fill_bars` (each `{"Open", "High", "Low", "Close"}`) describes.
    """
    index = pd.bdate_range("2022-12-27", periods=10)
    data = _flat_ohlcv(index)
    for offset, bar in enumerate(post_fill_bars):
        data["Open"][7 + offset] = bar["Open"]
        data["High"][7 + offset] = bar["High"]
        data["Low"][7 + offset] = bar["Low"]
        data["Close"][7 + offset] = bar["Close"]
    return pd.DataFrame(data, index=index)


# Signal bar (index 5) = "2023-01-03"; NEXT_OPEN fills on index 6 =
# "2023-01-04" at that bar's Open (100.0, from the flat warmup).
_ENTRY_DATE = "2023-01-03"
_FILL_DATE = "2023-01-04"


@pytest.fixture
def exit_prices(monkeypatch):
    universe = {
        "STOPHIT": _exit_resolution_frame(
            [
                {"Open": 98.0, "High": 102.0, "Low": 97.0, "Close": 99.0},
                # stop_loss = 100 * (1 - 0.05) = 95 -> Low=94 triggers it.
                {"Open": 99.0, "High": 100.0, "Low": 94.0, "Close": 95.0},
                {"Open": 95.0, "High": 97.0, "Low": 93.0, "Close": 94.0},
            ]
        ),
        "TARGETHIT": _exit_resolution_frame(
            [
                {"Open": 105.0, "High": 125.0, "Low": 99.0, "Close": 120.0},
                # take_profit = 100 * (1 + 0.30) = 130 -> High=135 triggers it.
                {"Open": 121.0, "High": 135.0, "Low": 118.0, "Close": 130.0},
                {"Open": 130.0, "High": 132.0, "Low": 128.0, "Close": 129.0},
            ]
        ),
        "TIMEOUTX": _exit_resolution_frame(
            [
                # Stays inside [95, 130] for all 3 post-fill bars - neither
                # stop nor target ever touched.
                {"Open": 100.0, "High": 105.0, "Low": 97.0, "Close": 102.0},
                {"Open": 102.0, "High": 106.0, "Low": 98.0, "Close": 101.0},
                {"Open": 101.0, "High": 104.0, "Low": 99.0, "Close": 100.0},
            ]
        ),
    }

    def _load_prices(ticker, start=None, end=None, data_dir=None, allow_download=True):
        ticker = ticker.upper()
        if ticker not in universe:
            raise DataUnavailableError(f"{ticker} not in test universe")
        return universe[ticker]

    import backend.app.api.replay as replay_module

    monkeypatch.setattr(replay_module, "load_prices", _load_prices)
    return universe


def _simulate(client, ticker, **overrides):
    payload = {
        "ticker": ticker,
        "entry_date": _ENTRY_DATE,
        "sl_type": "PERCENTAGE",
        "sl_value": 0.05,
        "tp_type": "PERCENTAGE",
        "tp_value": 0.30,
        "execution_mode": "NEXT_OPEN",
        "slippage_pct": 0.0,
        "use_regime_filter": False,
    }
    payload.update(overrides)
    return client.post("/api/v1/backtest/simulate-trade-execution", json=payload)


class TestSimulateTradeExecutionExitResolution:
    def test_stop_loss_resolves_first(self, client, exit_prices):
        response = _simulate(client, "STOPHIT")
        assert response.status_code == 200
        body = response.json()
        assert body["fill_price"] == 100.0
        assert body["exit_trigger"] == "STOP"
        assert body["exit_price"] == pytest.approx(95.0)
        assert body["exit_date"] == "2023-01-06"
        assert body["holding_period_days"] == 2
        assert body["mae_pct"] == pytest.approx(0.06)
        assert body["mfe_pct"] == pytest.approx(0.02)
        assert body["realized_pnl_dollars"] < 0
        assert body["realized_pnl_pct"] == pytest.approx(-0.05)

    def test_take_profit_resolves_before_a_later_stop_touch(self, client, exit_prices):
        response = _simulate(client, "TARGETHIT")
        assert response.status_code == 200
        body = response.json()
        assert body["exit_trigger"] == "TARGET"
        assert body["exit_price"] == pytest.approx(130.0)
        assert body["exit_date"] == "2023-01-06"
        assert body["holding_period_days"] == 2
        assert body["mae_pct"] == pytest.approx(0.01)
        assert body["mfe_pct"] == pytest.approx(0.35)
        assert body["realized_pnl_dollars"] > 0
        assert body["realized_pnl_pct"] == pytest.approx(0.30)

    def test_timeout_when_neither_stop_nor_target_is_touched(self, client, exit_prices):
        response = _simulate(client, "TIMEOUTX", max_holding_period_days=3)
        assert response.status_code == 200
        body = response.json()
        assert body["exit_trigger"] == "TIMEOUT"
        assert body["exit_price"] == pytest.approx(100.0)
        assert body["exit_date"] == "2023-01-09"
        assert body["holding_period_days"] == 5
        assert body["mae_pct"] == pytest.approx(0.03)
        assert body["mfe_pct"] == pytest.approx(0.06)
        assert body["realized_pnl_dollars"] == pytest.approx(0.0)

    def test_resolve_exit_false_keeps_prior_entry_fill_only_shape(
        self, client, exit_prices
    ):
        response = _simulate(client, "STOPHIT", resolve_exit=False)
        assert response.status_code == 200
        body = response.json()
        assert body["exit_trigger"] is None
        assert body["realized_pnl_dollars"] is None
        assert body["mae_pct"] is None
        # Pre-existing entry-fill fields are untouched.
        assert body["fill_price"] == 100.0
        assert body["tradable"] is True


class TestResolveTradeExitUnit:
    """Direct unit coverage of `quant.engine.resolve_trade_exit`'s MAE/MFE
    math against a hand-worked path, independent of the HTTP layer."""

    def test_mae_mfe_over_the_actual_path_not_just_the_endpoints(self):
        from backend.app.quant.engine import resolve_trade_exit

        index = pd.bdate_range("2023-02-01", periods=4)
        df = pd.DataFrame(
            {
                "Open": [100.0, 100.0, 92.0, 96.0],
                "High": [100.0, 108.0, 93.0, 99.0],
                "Low": [100.0, 91.0, 90.0, 95.0],
                "Close": [100.0, 92.0, 92.0, 97.0],
            },
            index=index,
        )
        # entry filled on bar 0 at 100; walk starts at bar 1.
        # Bar 1: High 108 -> MFE so far = 8% (endpoint-to-endpoint would
        # have missed this entirely, since price ends up below entry).
        # Bar 2: Low 90 -> stop (set at 90.5) triggers: MAE = 10%, not the
        # 8%-favorable a naive close-to-close read would report.
        result = resolve_trade_exit(
            df,
            fill_bar_index=0,
            entry_price=100.0,
            stop_loss=90.5,
            take_profit=130.0,
            direction=1,
            use_regime_filter=False,
        )
        assert result.exit_trigger == "STOP"
        assert result.exit_price == pytest.approx(90.5)
        assert result.mfe_pct == pytest.approx(0.08)
        assert result.mae_pct == pytest.approx(0.10)
        assert result.holding_period_days == 2

    def test_raises_when_no_bars_after_fill(self):
        from backend.app.quant.engine import resolve_trade_exit

        index = pd.bdate_range("2023-02-01", periods=1)
        df = pd.DataFrame(
            {"Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.0]},
            index=index,
        )
        with pytest.raises(ValueError):
            resolve_trade_exit(
                df,
                fill_bar_index=0,
                entry_price=100.0,
                stop_loss=95.0,
                take_profit=110.0,
            )
