"""Cycle 03 step 4: shortlist orders carry their strategy as Alpaca's
`client_order_id`, so the forward journal can attribute each fill."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.app.api.execution as execution_module
from backend.app.config import Settings, get_settings
from backend.app.execution.alpaca_client import AlpacaExecutionClient
from backend.app.main import app

TAG = "st-2026-11-24-zscore_mean_reversion-BRK.B"


class _Enum:
    def __init__(self, value):
        self.value = value


class _Recorder:
    def __init__(self):
        self.orders = []

    def submit_order(self, order_data):
        self.orders.append(order_data)
        return SimpleNamespace(
            id="order-1",
            symbol=order_data.symbol,
            qty=str(order_data.qty),
            side=_Enum("buy"),
            type=_Enum("market"),
            order_class=_Enum("bracket"),
            status=_Enum("accepted"),
            submitted_at=None,
        )

    def get_all_positions(self):
        return []


def test_alpaca_bracket_carries_client_order_id():
    fake = _Recorder()
    AlpacaExecutionClient("k", "s", client=fake).submit_bracket_order(
        "AAPL", 5, "buy", stop_loss=90.0, take_profit=120.0, client_order_id=TAG
    )
    assert fake.orders[0].client_order_id == TAG


def test_alpaca_bracket_without_tag_leaves_it_unset():
    fake = _Recorder()
    AlpacaExecutionClient("k", "s", client=fake).submit_bracket_order(
        "AAPL", 5, "buy", stop_loss=90.0, take_profit=120.0
    )
    assert fake.orders[0].client_order_id is None


@pytest.fixture
def api(market):
    fake = _Recorder()
    app.dependency_overrides[get_settings] = lambda: Settings()
    app.dependency_overrides[execution_module._client] = lambda: AlpacaExecutionClient(
        "k", "s", client=fake
    )
    yield TestClient(app), fake
    app.dependency_overrides.pop(execution_module._client, None)
    app.dependency_overrides.pop(get_settings, None)


def _order(**extra):
    return {
        "ticker": "BRK.B",
        "side": "buy",
        "qty": 5,
        "order_type": "BRACKET",
        "stop_loss": 90.0,
        "take_profit": 120.0,
        "intent": "ENTRY",
        **extra,
    }


def test_orders_route_passes_the_tag_through(api):
    client, fake = api
    res = client.post("/api/v1/execution/orders", json=_order(client_order_id=TAG))
    assert res.status_code == 200, res.text
    assert fake.orders[0].client_order_id == TAG


@pytest.mark.parametrize("bad", ["has space", "semi;colon", "x" * 129, ""])
def test_orders_route_rejects_a_malformed_tag(api, bad):
    client, fake = api
    res = client.post("/api/v1/execution/orders", json=_order(client_order_id=bad))
    assert res.status_code == 422
    assert fake.orders == []


def test_tagged_entry_still_runs_the_guards(api, market):
    client, fake = api
    market.state = "CLOSED"
    res = client.post("/api/v1/execution/orders", json=_order(client_order_id=TAG))
    assert res.status_code == 422
    assert fake.orders == []
