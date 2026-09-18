"""Tests for `/api/v1/execution/*` - Alpaca paper trading.

Every test injects a fake `AlpacaExecutionClient` via FastAPI's dependency
override; none touches the network or a real Alpaca account. Dispatch-guard
behavior (`TestDispatchGuards`) is exercised separately with the clock and
earnings/split calendars monkeypatched - every other test here disables
guards via a `get_settings` override so they stay focused on Alpaca dispatch
plumbing, not incidentally depending on session guard.
"""

from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backend.app.api.execution as execution_module
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
def _clear_overrides():
    app.dependency_overrides[get_settings] = lambda: Settings(
        execution_guards_enabled=False
    )
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


class TestDispatchGuards:
    """`_enforce_dispatch_guards` wiring - clock and calendars are all
    monkeypatched so nothing here depends on wall-clock time or the network."""

    def _enable_guards(self, monkeypatch, *, now, earnings=None, splits=None):
        app.dependency_overrides[get_settings] = lambda: Settings(
            execution_guards_enabled=True
        )
        monkeypatch.setattr(execution_module, "_current_moment", lambda: now)
        monkeypatch.setattr(
            execution_module, "fetch_earnings_dates", lambda ticker: earnings or []
        )
        monkeypatch.setattr(
            execution_module, "fetch_stock_splits", lambda ticker: splits or []
        )

    def test_regular_session_no_catalyst_dispatches(self, client, monkeypatch):
        _override_with_configured_client()
        # Wednesday 10:00 ET - regular session.
        self._enable_guards(monkeypatch, now=pd.Timestamp("2026-09-16 10:00"))
        response = client.post(
            "/api/v1/execution/orders",
            json={"ticker": "AAPL", "side": "buy", "qty": 10, "order_type": "MARKET"},
        )
        assert response.status_code == 200

    def test_weekend_dispatch_is_422(self, client, monkeypatch):
        _override_with_configured_client()
        # Saturday.
        self._enable_guards(monkeypatch, now=pd.Timestamp("2026-09-19 10:00"))
        response = client.post(
            "/api/v1/execution/orders",
            json={"ticker": "AAPL", "side": "buy", "qty": 10, "order_type": "MARKET"},
        )
        assert response.status_code == 422
        assert "closed" in response.json()["detail"].lower()

    def test_earnings_lockout_blocks_dispatch(self, client, monkeypatch):
        _override_with_configured_client()
        self._enable_guards(
            monkeypatch,
            now=pd.Timestamp("2026-09-16 10:00"),
            earnings=[pd.Timestamp("2026-09-17")],
        )
        response = client.post(
            "/api/v1/execution/orders",
            json={"ticker": "AAPL", "side": "buy", "qty": 10, "order_type": "MARKET"},
        )
        assert response.status_code == 422
        assert "earnings" in response.json()["detail"].lower()

    def test_split_lockout_blocks_dispatch(self, client, monkeypatch):
        _override_with_configured_client()
        self._enable_guards(
            monkeypatch,
            now=pd.Timestamp("2026-09-16 10:00"),
            splits=[pd.Timestamp("2026-09-15")],
        )
        response = client.post(
            "/api/v1/execution/orders",
            json={"ticker": "AAPL", "side": "buy", "qty": 10, "order_type": "MARKET"},
        )
        assert response.status_code == 422
        assert "split" in response.json()["detail"].lower()

    def test_close_all_is_never_guarded(self, client, monkeypatch):
        """The kill-switch must work even outside market hours."""
        _override_with_configured_client()
        self._enable_guards(monkeypatch, now=pd.Timestamp("2026-09-19 10:00"))
        response = client.post("/api/v1/execution/close-all", json={"confirm": True})
        assert response.status_code == 200

    def test_earnings_calendar_unavailable_fails_open(self, client, monkeypatch):
        """A data-provider failure on the earnings/split lookup must not
        block dispatch - only an actual catalyst date, or the clock, does."""
        from backend.app.data.loader import DataUnavailableError

        _override_with_configured_client()
        app.dependency_overrides[get_settings] = lambda: Settings(
            execution_guards_enabled=True
        )
        monkeypatch.setattr(
            execution_module,
            "_current_moment",
            lambda: pd.Timestamp("2026-09-16 10:00"),
        )

        def _raise(ticker):
            raise DataUnavailableError("provider down")

        monkeypatch.setattr(execution_module, "fetch_earnings_dates", _raise)
        monkeypatch.setattr(execution_module, "fetch_stock_splits", _raise)

        response = client.post(
            "/api/v1/execution/orders",
            json={"ticker": "AAPL", "side": "buy", "qty": 10, "order_type": "MARKET"},
        )
        assert response.status_code == 200


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
