"""
Alpaca paper-trading execution wrapper.

`TradingClient` is always constructed with `paper=True`, hardcoded - there
is no setting anywhere in this codebase that flips it to a live brokerage
account. This wraps the SDK's request/response shapes into plain dicts so
the API layer (and its tests) never need to import `alpaca` types directly.
"""

from __future__ import annotations

from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

VALID_SIDES = frozenset({"buy", "sell"})


class AlpacaNotConfiguredError(RuntimeError):
    """Raised by every `AlpacaExecutionClient` method when the client has no
    API key/secret - never silently no-ops in a way a caller could mistake
    for a placed order."""


def _side(side: str) -> OrderSide:
    normalized = side.lower()
    if normalized not in VALID_SIDES:
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    return OrderSide.BUY if normalized == "buy" else OrderSide.SELL


def _order_to_dict(order: Any) -> dict:
    return {
        "id": str(order.id),
        "symbol": order.symbol,
        "qty": str(order.qty) if order.qty is not None else None,
        "side": str(order.side.value) if order.side is not None else None,
        "type": str(order.type.value) if order.type is not None else None,
        "order_class": (
            str(order.order_class.value) if order.order_class is not None else None
        ),
        "status": str(order.status.value) if order.status is not None else None,
        "submitted_at": (
            order.submitted_at.isoformat() if order.submitted_at else None
        ),
    }


def _close_response_to_dict(response: Any) -> dict:
    return {
        "symbol": getattr(response, "symbol", None),
        "status": getattr(response, "status", None),
        "order_id": (
            str(response.body.id)
            if getattr(response, "body", None) is not None
            else None
        ),
    }


class AlpacaExecutionClient:
    """Thin, always-paper wrapper around `alpaca.trading.client.TradingClient`.

    Args:
        api_key: Alpaca API key, or `None` if not configured.
        api_secret: Alpaca API secret, or `None` if not configured.
        client: Pre-built `TradingClient` to use instead of constructing one
            lazily - for tests, so a fake/mock client can be injected
            without this class ever touching the network.
    """

    def __init__(
        self,
        api_key: str | None,
        api_secret: str | None,
        client: TradingClient | None = None,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self._client = client

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _require_client(self) -> TradingClient:
        if not self.is_configured:
            raise AlpacaNotConfiguredError(
                "Alpaca is not configured: set ALPACA_API_KEY and "
                "ALPACA_API_SECRET to place paper-trading orders."
            )
        if self._client is None:
            self._client = TradingClient(self.api_key, self.api_secret, paper=True)
        return self._client

    def submit_market_order(self, ticker: str, qty: float, side: str) -> dict:
        """Places a day market order. Raises `AlpacaNotConfiguredError` if
        no credentials are set."""
        client = self._require_client()
        request = MarketOrderRequest(
            symbol=ticker.upper(),
            qty=qty,
            side=_side(side),
            time_in_force=TimeInForce.DAY,
        )
        return _order_to_dict(client.submit_order(request))

    def submit_limit_order(
        self, ticker: str, qty: float, side: str, limit_price: float
    ) -> dict:
        """Places a day limit order. Raises `AlpacaNotConfiguredError` if no
        credentials are set."""
        client = self._require_client()
        request = LimitOrderRequest(
            symbol=ticker.upper(),
            qty=qty,
            side=_side(side),
            time_in_force=TimeInForce.DAY,
            limit_price=limit_price,
        )
        return _order_to_dict(client.submit_order(request))

    def submit_bracket_order(
        self,
        ticker: str,
        qty: float,
        side: str,
        stop_loss: float,
        take_profit: float,
        limit_price: float | None = None,
    ) -> dict:
        """Places a bracket (entry + OCO stop-loss/take-profit) order - a
        market entry if `limit_price` is `None`, otherwise a limit entry.
        Bracket orders require GTC time-in-force. Raises
        `AlpacaNotConfiguredError` if no credentials are set."""
        client = self._require_client()
        common: dict[str, Any] = dict(
            symbol=ticker.upper(),
            qty=qty,
            side=_side(side),
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=take_profit),
            stop_loss=StopLossRequest(stop_price=stop_loss),
        )
        request = (
            MarketOrderRequest(**common)
            if limit_price is None
            else LimitOrderRequest(limit_price=limit_price, **common)
        )
        return _order_to_dict(client.submit_order(request))

    def close_all_positions(self) -> list[dict]:
        """Emergency kill-switch: liquidates every open position and cancels
        every open order. Raises `AlpacaNotConfiguredError` if no
        credentials are set."""
        client = self._require_client()
        responses = client.close_all_positions(cancel_orders=True)
        return [_close_response_to_dict(r) for r in responses]

    def get_account(self) -> dict:
        """Account equity/cash/buying-power snapshot. Raises
        `AlpacaNotConfiguredError` if no credentials are set."""
        client = self._require_client()
        account = client.get_account()
        return {
            "account_number": account.account_number,
            "status": str(account.status.value),
            "equity": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "portfolio_value": float(account.portfolio_value),
        }
