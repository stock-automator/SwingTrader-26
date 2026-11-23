"""Tests for `/api/v1/execution/*` - Alpaca paper trading.

Every test injects a fake `AlpacaExecutionClient` via FastAPI's dependency
override; none touches the network or a real Alpaca account. Dispatch-guard
behavior (`TestDispatchGuards`) is exercised with the `market` fixture's fake
clock (conftest.py); every other test here runs with that clock in a REGULAR
session and empty earnings/split calendars, so they stay focused on Alpaca
dispatch plumbing. Guards can no longer be switched off (batch 02, F1).
"""

from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backend.app.api.execution as execution_module
import backend.app.execution.guards as guards_module
from backend.app.config import Settings, get_settings
from backend.app.execution.alpaca_client import (
    AlpacaExecutionClient,
    AlpacaNotConfiguredError,
)
from backend.app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides(market):
    app.dependency_overrides[get_settings] = lambda: Settings()
    yield
    app.dependency_overrides.pop(execution_module._client, None)
    app.dependency_overrides.pop(get_settings, None)


class _FakeEnumValue:
    def __init__(self, value):
        self.value = value


def _fake_order():
    return SimpleNamespace(
        id="order-1",
        symbol="AAPL",
        qty="10",
        side=_FakeEnumValue("buy"),
        type=_FakeEnumValue("market"),
        order_class=_FakeEnumValue("simple"),
        status=_FakeEnumValue("accepted"),
        submitted_at=None,
    )


class _FakeTradingClient:
    def submit_order(self, order_data):
        return _fake_order()

    def close_all_positions(self, cancel_orders=None):
        return [
            SimpleNamespace(symbol="AAPL", status=200, body=SimpleNamespace(id="c-1"))
        ]

    def get_account(self):
        return SimpleNamespace(
            account_number="PA1",
            status=_FakeEnumValue("ACTIVE"),
            equity="10000",
            cash="5000",
            buying_power="20000",
            portfolio_value="10000",
        )

    def get_all_positions(self):
        return [
            SimpleNamespace(
                symbol="AAPL",
                side=_FakeEnumValue("long"),
                qty="10",
                avg_entry_price="190.00",
                current_price="200.00",
                market_value="2000.00",
                cost_basis="1900.00",
                unrealized_pl="100.00",
                unrealized_plpc="0.0526",
            )
        ]

    def get_portfolio_history(self, history_filter=None):
        return SimpleNamespace(
            timestamp=[1704067200],
            equity=[10000.0],
            profit_loss=[0.0],
            profit_loss_pct=[0.0],
            base_value=10000.0,
            timeframe="1D",
        )


def _override_with_configured_client():
    fake_client = AlpacaExecutionClient("key", "secret", client=_FakeTradingClient())
    app.dependency_overrides[execution_module._client] = lambda: fake_client


class TestNotConfigured:
    def test_submit_order_is_503_when_unconfigured(self, client):
        app.dependency_overrides[execution_module._client] = (
            lambda: AlpacaExecutionClient(None, None)
        )
        response = client.post(
            "/api/v1/execution/orders",
            json={"ticker": "AAPL", "side": "buy", "qty": 10, "order_type": "MARKET"},
        )
        assert response.status_code == 503

    def test_account_is_503_when_unconfigured(self, client):
        app.dependency_overrides[execution_module._client] = (
            lambda: AlpacaExecutionClient(None, None)
        )
        response = client.get("/api/v1/execution/account")
        assert response.status_code == 503

    def test_close_all_is_503_when_unconfigured_even_with_confirm(self, client):
        app.dependency_overrides[execution_module._client] = (
            lambda: AlpacaExecutionClient(None, None)
        )
        response = client.post("/api/v1/execution/close-all", json={"confirm": True})
        assert response.status_code == 503

    def test_positions_is_503_when_unconfigured(self, client):
        app.dependency_overrides[execution_module._client] = (
            lambda: AlpacaExecutionClient(None, None)
        )
        response = client.get("/api/v1/execution/positions")
        assert response.status_code == 503

    def test_portfolio_history_is_503_when_unconfigured(self, client):
        app.dependency_overrides[execution_module._client] = (
            lambda: AlpacaExecutionClient(None, None)
        )
        response = client.get("/api/v1/execution/portfolio-history")
        assert response.status_code == 503


class TestSubmitOrder:
    def test_market_order_succeeds(self, client):
        _override_with_configured_client()
        response = client.post(
            "/api/v1/execution/orders",
            json={"ticker": "aapl", "side": "buy", "qty": 10, "order_type": "MARKET"},
        )
        assert response.status_code == 200
        assert response.json()["symbol"] == "AAPL"

    def test_limit_order_without_limit_price_is_422(self, client):
        _override_with_configured_client()
        response = client.post(
            "/api/v1/execution/orders",
            json={"ticker": "AAPL", "side": "buy", "qty": 10, "order_type": "LIMIT"},
        )
        assert response.status_code == 422

    def test_bracket_order_without_stop_loss_is_422(self, client):
        _override_with_configured_client()
        response = client.post(
            "/api/v1/execution/orders",
            json={
                "ticker": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "BRACKET",
                "take_profit": 210.0,
            },
        )
        assert response.status_code == 422

    def test_bracket_order_with_full_payload_succeeds(self, client):
        _override_with_configured_client()
        response = client.post(
            "/api/v1/execution/orders",
            json={
                "ticker": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "BRACKET",
                "stop_loss": 180.0,
                "take_profit": 210.0,
            },
        )
        assert response.status_code == 200

    def test_invalid_order_type_is_422(self, client):
        _override_with_configured_client()
        response = client.post(
            "/api/v1/execution/orders",
            json={"ticker": "AAPL", "side": "buy", "qty": 10, "order_type": "STOP"},
        )
        assert response.status_code == 422


class TestCloseAll:
    def test_requires_explicit_confirmation(self, client):
        _override_with_configured_client()
        response = client.post("/api/v1/execution/close-all", json={"confirm": False})
        assert response.status_code == 422

    def test_confirmed_close_all_succeeds(self, client):
        _override_with_configured_client()
        response = client.post("/api/v1/execution/close-all", json={"confirm": True})
        assert response.status_code == 200
        assert response.json()["closed"][0]["symbol"] == "AAPL"


class TestAccount:
    def test_returns_account_snapshot(self, client):
        _override_with_configured_client()
        response = client.get("/api/v1/execution/account")
        assert response.status_code == 200
        assert response.json()["equity"] == 10000.0


class TestPositions:
    def test_returns_open_positions(self, client):
        _override_with_configured_client()
        response = client.get("/api/v1/execution/positions")
        assert response.status_code == 200
        positions = response.json()["positions"]
        assert positions == [
            {
                "symbol": "AAPL",
                "side": "long",
                "qty": 10.0,
                "avg_entry_price": 190.0,
                "current_price": 200.0,
                "market_value": 2000.0,
                "cost_basis": 1900.0,
                "unrealized_pl": 100.0,
                "unrealized_plpc": 0.0526,
            }
        ]


class TestPortfolioHistory:
    def test_returns_equity_curve(self, client):
        _override_with_configured_client()
        response = client.get("/api/v1/execution/portfolio-history")
        assert response.status_code == 200
        body = response.json()
        assert body["equity"] == [10000.0]
        assert body["timeframe"] == "1D"


class TestDispatchGuards:
    """`enforce_order_guards` wiring - the fake clock and the calendars on
    `execution.guards` are set per test, so nothing here depends on
    wall-clock time or the network."""

    def _set(self, monkeypatch, market, *, state="REGULAR", earnings=None, splits=None):
        market.state = state
        monkeypatch.setattr(
            guards_module, "fetch_earnings_dates", lambda t, *a, **k: earnings or []
        )
        monkeypatch.setattr(
            guards_module, "fetch_stock_splits", lambda t, *a, **k: splits or []
        )

    def _buy(self, client):
        return client.post(
            "/api/v1/execution/orders",
            json={"ticker": "AAPL", "side": "buy", "qty": 10, "order_type": "MARKET"},
        )

    def test_regular_session_no_catalyst_dispatches(self, client, monkeypatch, market):
        _override_with_configured_client()
        self._set(monkeypatch, market)
        assert self._buy(client).status_code == 200

    def test_weekend_dispatch_is_422(self, client, monkeypatch, market):
        _override_with_configured_client()
        self._set(monkeypatch, market, state="CLOSED")
        response = self._buy(client)
        assert response.status_code == 422
        assert "closed" in str(response.json()["detail"]).lower()

    def test_earnings_lockout_blocks_dispatch(self, client, monkeypatch, market):
        _override_with_configured_client()
        self._set(monkeypatch, market, earnings=[_tomorrow()])
        response = self._buy(client)
        assert response.status_code == 422
        assert "earnings" in str(response.json()["detail"]).lower()

    def test_split_lockout_blocks_dispatch(self, client, monkeypatch, market):
        _override_with_configured_client()
        self._set(monkeypatch, market, splits=[_tomorrow()])
        response = self._buy(client)
        assert response.status_code == 422
        assert "split" in str(response.json()["detail"]).lower()

    def test_close_all_is_never_guarded(self, client, monkeypatch, market):
        """The kill-switch must work even outside market hours."""
        _override_with_configured_client()
        self._set(monkeypatch, market, state="CLOSED")
        response = client.post("/api/v1/execution/close-all", json={"confirm": True})
        assert response.status_code == 200

    def test_earnings_calendar_unavailable_fails_closed(
        self, client, monkeypatch, market
    ):
        """F1: a data-provider failure on the earnings/split lookup now
        blocks a new entry (it used to fail open)."""
        from backend.app.data.loader import DataUnavailableError

        _override_with_configured_client()

        def _raise(ticker, *a, **k):
            raise DataUnavailableError("provider down")

        monkeypatch.setattr(guards_module, "fetch_earnings_dates", _raise)
        monkeypatch.setattr(guards_module, "fetch_stock_splits", _raise)

        response = self._buy(client)
        assert response.status_code == 422
        assert "unavailable" in str(response.json()["detail"]).lower()


def _tomorrow() -> pd.Timestamp:
    return pd.Timestamp.now().normalize() + pd.Timedelta(days=1)


class TestClientRaisesMidRequest:
    def test_not_configured_error_raised_inside_a_route_is_mapped_to_503(self, client):
        class _RaisingClient(AlpacaExecutionClient):
            def __init__(self):
                super().__init__("key", "secret")

            def submit_market_order(self, *a, **k):
                raise AlpacaNotConfiguredError("revoked mid-flight")

        app.dependency_overrides[execution_module._client] = _RaisingClient
        response = client.post(
            "/api/v1/execution/orders",
            json={"ticker": "AAPL", "side": "buy", "qty": 10, "order_type": "MARKET"},
        )
        assert response.status_code == 503
