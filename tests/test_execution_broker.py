"""
Tests for `backend.app.execution.broker`: the `ExecutionBroker` interface
and its `PaperBroker`/`AlpacaBroker` adapters. `AlpacaBroker` is exercised
against a fake `AlpacaExecutionClient`-shaped object, not the real SDK -
mirroring `tests/test_alpaca_client.py`'s own fake-client convention.
"""

from types import SimpleNamespace

import pytest

from backend.app.execution.alpaca_client import AlpacaNotConfiguredError
from backend.app.execution.broker import (
    STATUS_CANCELLED,
    STATUS_FILLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    AlpacaBroker,
    PaperBroker,
)


class TestPaperBrokerConstruction:
    def test_rejects_negative_spread(self):
        with pytest.raises(ValueError):
            PaperBroker(spread_bps=-1.0)


class TestPaperBrokerSubmit:
    def test_market_buy_fills_above_reference_price(self):
        broker = PaperBroker(spread_bps=10.0)
        result = broker.submit("AAPL", "buy", 10, reference_price=100.0)
        assert result.status == STATUS_FILLED
        assert result.fill_price == pytest.approx(100.10)

    def test_market_sell_fills_below_reference_price(self):
        broker = PaperBroker(spread_bps=10.0)
        result = broker.submit("AAPL", "sell", 10, reference_price=100.0)
        assert result.status == STATUS_FILLED
        assert result.fill_price == pytest.approx(99.90)

    def test_market_without_reference_price_is_rejected(self):
        broker = PaperBroker()
        result = broker.submit("AAPL", "buy", 10)
        assert result.status == STATUS_REJECTED
        assert result.fill_price is None
        assert "reference_price" in result.error

    def test_limit_order_fills_at_limit_price_exactly(self):
        broker = PaperBroker(spread_bps=50.0)
        result = broker.submit(
            "AAPL", "buy", 10, order_type="LIMIT", limit_price=101.5
        )
        assert result.status == STATUS_FILLED
        assert result.fill_price == 101.5

    def test_limit_order_without_price_is_rejected(self):
        broker = PaperBroker()
        result = broker.submit("AAPL", "buy", 10, order_type="LIMIT")
        assert result.status == STATUS_REJECTED

    def test_invalid_side_raises(self):
        broker = PaperBroker()
        with pytest.raises(ValueError):
            broker.submit("AAPL", "hold", 10, reference_price=100.0)

    def test_nonpositive_qty_raises(self):
        broker = PaperBroker()
        with pytest.raises(ValueError):
            broker.submit("AAPL", "buy", 0, reference_price=100.0)

    def test_cancel_always_declines(self):
        broker = PaperBroker()
        result = broker.submit("AAPL", "buy", 10, reference_price=100.0)
        assert broker.cancel(result.broker_order_id) is False


def _fake_client(configured=True, submit_result=None, cancel_result=True, submit_raises=None):
    client = SimpleNamespace()
    client.is_configured = configured

    def submit_market_order(ticker, qty, side):
        if submit_raises:
            raise submit_raises
        return submit_result or {"id": "alpaca-1", "status": "accepted"}

    def submit_limit_order(ticker, qty, side, limit_price):
        if submit_raises:
            raise submit_raises
        return submit_result or {"id": "alpaca-2", "status": "accepted"}

    def cancel_order(order_id):
        return cancel_result

    client.submit_market_order = submit_market_order
    client.submit_limit_order = submit_limit_order
    client.cancel_order = cancel_order
    return client


class TestAlpacaBrokerSubmit:
    def test_market_order_maps_pending_status(self):
        client = _fake_client(submit_result={"id": "abc", "status": "accepted"})
        broker = AlpacaBroker(client)
        result = broker.submit("AAPL", "buy", 5)
        assert result.status == STATUS_PENDING
        assert result.broker_order_id == "abc"

    def test_filled_status_maps_to_filled(self):
        client = _fake_client(submit_result={"id": "abc", "status": "filled"})
        broker = AlpacaBroker(client)
        result = broker.submit("AAPL", "buy", 5)
        assert result.status == STATUS_FILLED

    def test_canceled_status_maps_to_cancelled(self):
        client = _fake_client(submit_result={"id": "abc", "status": "canceled"})
        broker = AlpacaBroker(client)
        result = broker.submit("AAPL", "buy", 5)
        assert result.status == STATUS_CANCELLED

    def test_limit_order_requires_limit_price(self):
        client = _fake_client()
        broker = AlpacaBroker(client)
        result = broker.submit("AAPL", "buy", 5, order_type="LIMIT")
        assert result.status == STATUS_REJECTED

    def test_not_configured_client_is_rejected_not_raised(self):
        client = _fake_client(submit_raises=AlpacaNotConfiguredError("no keys"))
        broker = AlpacaBroker(client)
        result = broker.submit("AAPL", "buy", 5)
        assert result.status == STATUS_REJECTED
        assert "no keys" in result.error

    def test_invalid_side_is_rejected_not_raised(self):
        client = _fake_client()
        broker = AlpacaBroker(client)
        result = broker.submit("AAPL", "hold", 5)
        assert result.status == STATUS_REJECTED


class TestAlpacaBrokerCancel:
    def test_cancel_success(self):
        client = _fake_client(cancel_result=True)
        broker = AlpacaBroker(client)
        assert broker.cancel("abc") is True

    def test_cancel_not_configured_returns_false(self):
        client = _fake_client()
        client.cancel_order = lambda order_id: (_ for _ in ()).throw(
            AlpacaNotConfiguredError("no keys")
        )
        broker = AlpacaBroker(client)
        assert broker.cancel("abc") is False
