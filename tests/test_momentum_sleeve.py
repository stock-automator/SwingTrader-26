"""Momentum sleeve (6-1, top 10, equal weight, 2N hold buffer, no SPY filter)
in the Alpaca paper account, alongside the swing executor. No network: HTTP,
the market calendar and the price cache are all faked."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.app.api.execution as execution_module
from backend.app.config import Settings, get_settings
from backend.app.execution.alpaca_client import AlpacaExecutionClient
from backend.app.main import app

# ------------------------------------------ MARKET orders carry the order tag


class _RecordingSdk:
    def __init__(self):
        self.submitted = []

    def submit_order(self, order_data):
        self.submitted.append(order_data)
        return SimpleNamespace(
            id="o-1",
            symbol=order_data.symbol,
            qty=str(order_data.qty),
            side=SimpleNamespace(value="buy"),
            type=SimpleNamespace(value="market"),
            order_class=SimpleNamespace(value="simple"),
            status=SimpleNamespace(value="accepted"),
            submitted_at=None,
        )


def test_market_order_sends_client_order_id_as_a_day_order():
    sdk = _RecordingSdk()
    client = AlpacaExecutionClient("key", "secret", client=sdk)
    client.submit_market_order("aapl", 1.234567, "buy", client_order_id="mom-x")
    request = sdk.submitted[0]
    assert request.client_order_id == "mom-x"
    assert request.qty == 1.234567
    assert request.time_in_force.value == "day"


@pytest.fixture
def api(market):
    sdk = _RecordingSdk()
    fake = AlpacaExecutionClient("key", "secret", client=sdk)
    fake.get_positions = lambda: [{"symbol": "AAPL", "side": "long", "qty": 3.0}]
    app.dependency_overrides[execution_module._client] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: Settings()
    yield TestClient(app), sdk
    app.dependency_overrides.pop(execution_module._client, None)
    app.dependency_overrides.pop(get_settings, None)


def test_execution_route_passes_the_tag_on_market_orders(api):
    client, sdk = api
    body = {
        "ticker": "AAPL",
        "side": "buy",
        "qty": 0.5,
        "order_type": "MARKET",
        "intent": "ENTRY",
        "client_order_id": "mom-2026-12-AAPL",
    }
    assert client.post("/api/v1/execution/orders", json=body).status_code == 200
    assert sdk.submitted[0].client_order_id == "mom-2026-12-AAPL"


def test_market_entry_without_stop_still_runs_the_guards(api, market):
    """No guard is relaxed for the sleeve: a closed session still blocks."""
    client, sdk = api
    market.state = "CLOSED"
    body = {"ticker": "AAPL", "side": "buy", "qty": 0.5, "intent": "ENTRY"}
    assert client.post("/api/v1/execution/orders", json=body).status_code == 422
    assert sdk.submitted == []


def test_sleeve_exit_of_held_quantity_passes_a_closed_session(api, market):
    client, sdk = api
    market.state = "CLOSED"
    body = {"ticker": "AAPL", "side": "sell", "qty": 3.0, "intent": "EXIT"}
    assert client.post("/api/v1/execution/orders", json=body).status_code == 200
    # More than the account holds is not an exit: it is guarded (no short).
    body["qty"] = 3.5
    assert client.post("/api/v1/execution/orders", json=body).status_code == 422
