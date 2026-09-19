"""
Tests for `/api/v1/orders/*` (`backend.app.api.orders`) - broker-agnostic
order submission/cancellation/listing, persisted to an isolated DuckDB file
per test (same `SCANS_DB_PATH` isolation pattern `test_screener_async.py`
uses for `routed_orders`' sibling tables).

`AlpacaBroker` paths are exercised via `execution.alpaca_client.
AlpacaExecutionClient` monkeypatched at the module level - never the real
SDK/network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.app.db.session as session
from backend.app.main import app


@pytest.fixture(autouse=True)
def _isolated_duckdb(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANS_DB_PATH", str(tmp_path / "orders.duckdb"))
    session.reset_for_tests()
    yield
    session.reset_for_tests()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestSubmitPaperOrder:
    def test_market_order_fills_immediately(self, client):
        response = client.post(
            "/api/v1/orders/submit",
            json={
                "broker": "PAPER",
                "ticker": "aapl",
                "side": "buy",
                "qty": 10,
                "order_type": "MARKET",
                "reference_price": 200.0,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "FILLED"
        assert body["ticker"] == "AAPL"
        assert body["fill_price"] > 200.0  # buy side pays the spread haircut

    def test_market_order_without_reference_price_is_422(self, client):
        response = client.post(
            "/api/v1/orders/submit",
            json={"broker": "PAPER", "ticker": "AAPL", "side": "buy", "qty": 10},
        )
        assert response.status_code == 422

    def test_limit_order_fills_at_limit_price(self, client):
        response = client.post(
            "/api/v1/orders/submit",
            json={
                "broker": "PAPER",
                "ticker": "MSFT",
                "side": "sell",
                "qty": 5,
                "order_type": "LIMIT",
                "limit_price": 410.25,
            },
        )
        assert response.status_code == 200
        assert response.json()["fill_price"] == 410.25

    def test_invalid_broker_is_422(self, client):
        response = client.post(
            "/api/v1/orders/submit",
            json={"broker": "ROBINHOOD", "ticker": "AAPL", "side": "buy", "qty": 1},
        )
        assert response.status_code == 422

    def test_filled_paper_order_does_not_appear_in_active_list(self, client):
        client.post(
            "/api/v1/orders/submit",
            json={
                "broker": "PAPER",
                "ticker": "AAPL",
                "side": "buy",
                "qty": 10,
                "reference_price": 100.0,
            },
        )
        response = client.get("/api/v1/orders/active")
        assert response.status_code == 200
        assert response.json()["orders"] == []


class TestSubmitAlpacaOrder:
    def _patch_client(self, monkeypatch, *, submit_return=None, is_configured=True):
        class _FakeAlpacaClient:
            def __init__(self, *a, **kw):
                self.is_configured = is_configured

            def submit_market_order(self, ticker, qty, side):
                return submit_return or {"id": "alpaca-order-1", "status": "accepted"}

            def submit_limit_order(self, ticker, qty, side, limit_price):
                return submit_return or {"id": "alpaca-order-1", "status": "accepted"}

            def cancel_order(self, order_id):
                return True

        monkeypatch.setattr(
            "backend.app.api.orders.AlpacaExecutionClient", _FakeAlpacaClient
        )

    def test_pending_alpaca_order_appears_in_active_list(self, client, monkeypatch):
        self._patch_client(monkeypatch)

        submit = client.post(
            "/api/v1/orders/submit",
            json={"broker": "ALPACA", "ticker": "AAPL", "side": "buy", "qty": 5},
        )
        assert submit.status_code == 200
        assert submit.json()["status"] == "PENDING"

        active = client.get("/api/v1/orders/active")
        assert len(active.json()["orders"]) == 1
        assert active.json()["orders"][0]["broker_order_id"] == "alpaca-order-1"

    def test_cancel_pending_order_succeeds(self, client, monkeypatch):
        self._patch_client(monkeypatch)

        submit = client.post(
            "/api/v1/orders/submit",
            json={"broker": "ALPACA", "ticker": "AAPL", "side": "buy", "qty": 5},
        )
        order_id = submit.json()["order_id"]

        cancel = client.post(f"/api/v1/orders/cancel/{order_id}")
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "CANCELLED"

        active = client.get("/api/v1/orders/active")
        assert active.json()["orders"] == []

    def test_cancel_unknown_order_is_404(self, client):
        response = client.post("/api/v1/orders/cancel/does-not-exist")
        assert response.status_code == 404

    def test_cancel_already_filled_order_is_422(self, client):
        submit = client.post(
            "/api/v1/orders/submit",
            json={
                "broker": "PAPER",
                "ticker": "AAPL",
                "side": "buy",
                "qty": 10,
                "reference_price": 100.0,
            },
        )
        order_id = submit.json()["order_id"]

        response = client.post(f"/api/v1/orders/cancel/{order_id}")
        assert response.status_code == 422
