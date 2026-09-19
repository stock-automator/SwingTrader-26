"""
Tests for `backend.app.execution.alpaca_client`.

Every test injects a fake `TradingClient` via the `client=` constructor
param - this suite never imports `alpaca.trading.client.TradingClient` for
real and never touches the network, paper or otherwise.
"""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from backend.app.execution.alpaca_client import (
    AlpacaExecutionClient,
    AlpacaNotConfiguredError,
)


@dataclass
class _FakeEnumValue:
    value: str


def _fake_order(**overrides) -> SimpleNamespace:
    defaults = dict(
        id="order-123",
        symbol="AAPL",
        qty="10",
        side=_FakeEnumValue("buy"),
        type=_FakeEnumValue("market"),
        order_class=_FakeEnumValue("simple"),
        status=_FakeEnumValue("accepted"),
        submitted_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _FakeTradingClient:
    """Records every call and returns canned responses - the injected
    stand-in for `alpaca.trading.client.TradingClient`."""

    def __init__(self):
        self.submitted_orders = []
        self.close_all_called = False
        self.cancelled_order_ids = []
        self.cancel_raises: Exception | None = None

    def submit_order(self, order_data):
        self.submitted_orders.append(order_data)
        return _fake_order()

    def cancel_order_by_id(self, order_id):
        if self.cancel_raises:
            raise self.cancel_raises
        self.cancelled_order_ids.append(order_id)

    def close_all_positions(self, cancel_orders=None):
        self.close_all_called = True
        self.cancel_orders_flag = cancel_orders
        return [
            SimpleNamespace(
                symbol="AAPL", status=200, body=SimpleNamespace(id="close-order-1")
            )
        ]

    def get_account(self):
        return SimpleNamespace(
            account_number="PA123",
            status=_FakeEnumValue("ACTIVE"),
            equity="10000.00",
            cash="5000.00",
            buying_power="20000.00",
            portfolio_value="10000.00",
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
        self.portfolio_history_filter = history_filter
        return SimpleNamespace(
            timestamp=[1704067200, 1704153600],
            equity=[10000.0, 10100.0],
            profit_loss=[0.0, 100.0],
            profit_loss_pct=[0.0, 0.01],
            base_value=10000.0,
            timeframe="1D",
        )


class TestNotConfigured:
    def test_every_method_raises_when_unconfigured(self):
        client = AlpacaExecutionClient(api_key=None, api_secret=None)
        assert client.is_configured is False

        with pytest.raises(AlpacaNotConfiguredError):
            client.submit_market_order("AAPL", 10, "buy")
        with pytest.raises(AlpacaNotConfiguredError):
            client.submit_limit_order("AAPL", 10, "buy", 190.0)
        with pytest.raises(AlpacaNotConfiguredError):
            client.submit_bracket_order("AAPL", 10, "buy", 180.0, 200.0)
        with pytest.raises(AlpacaNotConfiguredError):
            client.close_all_positions()
        with pytest.raises(AlpacaNotConfiguredError):
            client.get_account()
        with pytest.raises(AlpacaNotConfiguredError):
            client.get_positions()
        with pytest.raises(AlpacaNotConfiguredError):
            client.get_portfolio_history()

    def test_partial_credentials_still_count_as_unconfigured(self):
        client = AlpacaExecutionClient(api_key="key", api_secret=None)
        assert client.is_configured is False


class TestSubmitOrders:
    def test_market_order_builds_expected_request(self):
        fake = _FakeTradingClient()
        client = AlpacaExecutionClient("key", "secret", client=fake)

        result = client.submit_market_order("aapl", 10, "buy")

        assert len(fake.submitted_orders) == 1
        request = fake.submitted_orders[0]
        assert request.symbol == "AAPL"
        assert request.qty == 10
        assert result["symbol"] == "AAPL"
        assert result["status"] == "accepted"

    def test_limit_order_carries_limit_price(self):
        fake = _FakeTradingClient()
        client = AlpacaExecutionClient("key", "secret", client=fake)

        client.submit_limit_order("AAPL", 5, "sell", 195.5)

        request = fake.submitted_orders[0]
        assert request.limit_price == 195.5
        assert request.side.value == "sell"

    def test_bracket_order_market_entry_carries_tp_and_sl(self):
        fake = _FakeTradingClient()
        client = AlpacaExecutionClient("key", "secret", client=fake)

        client.submit_bracket_order(
            "AAPL", 10, "buy", stop_loss=180.0, take_profit=210.0
        )

        request = fake.submitted_orders[0]
        assert request.order_class.value == "bracket"
        assert request.take_profit.limit_price == 210.0
        assert request.stop_loss.stop_price == 180.0

    def test_bracket_order_with_limit_price_is_a_limit_entry(self):
        fake = _FakeTradingClient()
        client = AlpacaExecutionClient("key", "secret", client=fake)

        client.submit_bracket_order(
            "AAPL", 10, "buy", stop_loss=180.0, take_profit=210.0, limit_price=190.0
        )

        request = fake.submitted_orders[0]
        assert request.limit_price == 190.0

    def test_invalid_side_raises_value_error(self):
        fake = _FakeTradingClient()
        client = AlpacaExecutionClient("key", "secret", client=fake)
        with pytest.raises(ValueError):
            client.submit_market_order("AAPL", 10, "sideways")


class TestCancelOrder:
    def test_cancel_success_returns_true(self):
        fake = _FakeTradingClient()
        client = AlpacaExecutionClient("key", "secret", client=fake)

        assert client.cancel_order("order-123") is True
        assert fake.cancelled_order_ids == ["order-123"]

    def test_cancel_failure_returns_false_not_raises(self):
        fake = _FakeTradingClient()
        fake.cancel_raises = RuntimeError("already filled")
        client = AlpacaExecutionClient("key", "secret", client=fake)

        assert client.cancel_order("order-123") is False

    def test_cancel_raises_when_unconfigured(self):
        client = AlpacaExecutionClient(api_key=None, api_secret=None)
        with pytest.raises(AlpacaNotConfiguredError):
            client.cancel_order("order-123")


class TestCloseAllAndAccount:
    def test_close_all_positions_cancels_orders_and_returns_results(self):
        fake = _FakeTradingClient()
        client = AlpacaExecutionClient("key", "secret", client=fake)

        results = client.close_all_positions()

        assert fake.close_all_called is True
        assert fake.cancel_orders_flag is True
        assert results == [
            {"symbol": "AAPL", "status": 200, "order_id": "close-order-1"}
        ]

    def test_get_account_returns_a_plain_dict(self):
        fake = _FakeTradingClient()
        client = AlpacaExecutionClient("key", "secret", client=fake)

        account = client.get_account()

        assert account["account_number"] == "PA123"
        assert account["equity"] == 10000.0
        assert account["status"] == "ACTIVE"


class TestPositionsAndHistory:
    def test_get_positions_returns_plain_dicts(self):
        fake = _FakeTradingClient()
        client = AlpacaExecutionClient("key", "secret", client=fake)

        positions = client.get_positions()

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

    def test_get_portfolio_history_converts_timestamps_and_values(self):
        fake = _FakeTradingClient()
        client = AlpacaExecutionClient("key", "secret", client=fake)

        history = client.get_portfolio_history(period="1M", timeframe="1D")

        assert history["equity"] == [10000.0, 10100.0]
        assert history["base_value"] == 10000.0
        assert history["timeframe"] == "1D"
        assert len(history["timestamp"]) == 2
        assert history["timestamp"][0].startswith("2024-01-01")
        assert fake.portfolio_history_filter.period == "1M"
        assert fake.portfolio_history_filter.timeframe == "1D"
