"""
Decoupled order-routing interface: `ExecutionBroker` plus two adapters -
`PaperBroker` (simulated fills, no network, no real capital) and
`AlpacaBroker` (wraps the existing `execution.alpaca_client.
AlpacaExecutionClient`).

`api/orders.py` depends on `ExecutionBroker` alone, never on a concrete
adapter - which broker handles a request is a constructor choice made at
the route's dependency-injection boundary, not something baked into the
route logic. Both adapters return the same `BrokerOrderResult` shape, and
`api/orders.py` persists that verbatim to DuckDB (`routed_orders`, see
`db/session.py`) regardless of which adapter produced it - the same
"one shared table, thread-safe via the module lock" durability model
`api/scans.py` already uses for background scan jobs.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .alpaca_client import AlpacaExecutionClient, AlpacaNotConfiguredError

VALID_SIDES = frozenset({"buy", "sell"})
VALID_ORDER_TYPES = frozenset({"MARKET", "LIMIT"})

STATUS_PENDING = "PENDING"
STATUS_FILLED = "FILLED"
STATUS_CANCELLED = "CANCELLED"
STATUS_REJECTED = "REJECTED"


def _validate_submit_args(ticker: str, side: str, qty: float, order_type: str) -> str:
    """Shared input validation both adapters run before touching their
    respective backend - returns the normalized (lowercase) side.

    Raises:
        ValueError: on an empty ticker, non-positive qty, unknown side, or
            unknown order_type.
    """
    if not ticker:
        raise ValueError("ticker must not be empty")
    if not isinstance(qty, (int, float)) or qty <= 0:
        raise ValueError("qty must be positive")
    normalized_side = side.lower()
    if normalized_side not in VALID_SIDES:
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if order_type not in VALID_ORDER_TYPES:
        raise ValueError(f"order_type must be one of {sorted(VALID_ORDER_TYPES)}")
    return normalized_side


@dataclass(frozen=True)
class BrokerOrderResult:
    """Broker-agnostic outcome of a submit/cancel call."""

    broker_order_id: str
    status: str
    fill_price: float | None = None
    error: str | None = None


class ExecutionBroker(ABC):
    """Order-routing interface every broker adapter implements."""

    @abstractmethod
    def submit(
        self,
        ticker: str,
        side: str,
        qty: float,
        order_type: str = "MARKET",
        limit_price: float | None = None,
        reference_price: float | None = None,
    ) -> BrokerOrderResult:
        """Place an order. `reference_price` is only meaningful to
        `PaperBroker` (it has no live quote of its own to fill a MARKET
        order against) - `AlpacaBroker` ignores it, the real broker prices
        the fill itself."""

    @abstractmethod
    def cancel(self, broker_order_id: str) -> bool:
        """Whether the cancel request was accepted. `False` covers both
        "unknown order id" and "already filled/cancelled, nothing to
        cancel" - the caller (`api/orders.py`) reports either as a 422, not
        a 404 vs. 409 distinction neither adapter can reliably make."""


class PaperBroker(ExecutionBroker):
    """Simulated execution: fills a MARKET order immediately at
    `reference_price` plus a fixed spread haircut; a LIMIT order fills at
    exactly `limit_price`. No network, no real capital, always synchronous.

    Unlike `quant.slippage_model` (which prices one specific historical bar
    for backtest replay off that bar's own ATR/volume), a live paper order
    has no OHLCV window to draw a bar-aware cost estimate from - only "the
    current quote" - so this uses a flat `spread_bps` haircut instead. Good
    enough to make paper fills non-trivially different from the quoted
    price; not a substitute for `slippage_model`'s richer bar-aware model
    in an actual backtest.

    Args:
        spread_bps: Round-trip spread haircut in basis points, applied
            against the order's side - a buy fills above `reference_price`,
            a sell fills below it. E.g. `5.0` for 5bps.

    Raises:
        ValueError: if `spread_bps` is negative.
    """

    def __init__(self, spread_bps: float = 5.0):
        if spread_bps < 0:
            raise ValueError("spread_bps must be non-negative")
        self.spread_bps = spread_bps

    def submit(
        self,
        ticker: str,
        side: str,
        qty: float,
        order_type: str = "MARKET",
        limit_price: float | None = None,
        reference_price: float | None = None,
    ) -> BrokerOrderResult:
        normalized_side = _validate_submit_args(ticker, side, qty, order_type)
        broker_order_id = f"paper-{uuid.uuid4().hex[:12]}"

        if order_type == "LIMIT":
            if limit_price is None or limit_price <= 0:
                return BrokerOrderResult(
                    broker_order_id=broker_order_id,
                    status=STATUS_REJECTED,
                    error="limit_price is required (and must be positive) for a LIMIT order",
                )
            fill_price = limit_price
        else:
            if reference_price is None or reference_price <= 0:
                return BrokerOrderResult(
                    broker_order_id=broker_order_id,
                    status=STATUS_REJECTED,
                    error="reference_price is required to simulate a MARKET fill",
                )
            haircut = reference_price * (self.spread_bps / 10_000)
            fill_price = (
                reference_price + haircut
                if normalized_side == "buy"
                else reference_price - haircut
            )

        return BrokerOrderResult(
            broker_order_id=broker_order_id, status=STATUS_FILLED, fill_price=fill_price
        )

    def cancel(self, broker_order_id: str) -> bool:
        # Every paper fill above is synchronous/immediate - nothing is ever
        # left in a cancellable pending state, so this always reports
        # "nothing to cancel", the same outcome a real broker gives for an
        # already-filled order.
        return False


#: Alpaca order statuses (https://docs.alpaca.markets order lifecycle) that
#: map onto this module's four generic states. Anything not listed here
#: (`new`, `accepted`, `pending_new`, `partially_filled`, ...) is still an
#: open, working order from this platform's point of view - `PENDING`.
_ALPACA_FILLED_STATUSES = frozenset({"filled"})
_ALPACA_CANCELLED_STATUSES = frozenset({"canceled", "cancelled", "expired"})
_ALPACA_REJECTED_STATUSES = frozenset({"rejected"})


def _normalize_alpaca_status(raw_status: str | None) -> str:
    status = (raw_status or "").lower()
    if status in _ALPACA_FILLED_STATUSES:
        return STATUS_FILLED
    if status in _ALPACA_CANCELLED_STATUSES:
        return STATUS_CANCELLED
    if status in _ALPACA_REJECTED_STATUSES:
        return STATUS_REJECTED
    return STATUS_PENDING


class AlpacaBroker(ExecutionBroker):
    """Routes through the existing `AlpacaExecutionClient` (always
    paper-trading - see that module's docstring)."""

    def __init__(self, client: AlpacaExecutionClient):
        self._client = client

    def submit(
        self,
        ticker: str,
        side: str,
        qty: float,
        order_type: str = "MARKET",
        limit_price: float | None = None,
        reference_price: float | None = None,
    ) -> BrokerOrderResult:
        del reference_price  # Alpaca prices its own fill; unused here.
        broker_order_id = f"alpaca-rejected-{uuid.uuid4().hex[:8]}"
        try:
            _validate_submit_args(ticker, side, qty, order_type)
            if order_type == "LIMIT":
                if limit_price is None:
                    raise ValueError("limit_price is required for a LIMIT order")
                result = self._client.submit_limit_order(ticker, qty, side, limit_price)
            else:
                result = self._client.submit_market_order(ticker, qty, side)
        except (ValueError, AlpacaNotConfiguredError) as exc:
            return BrokerOrderResult(
                broker_order_id=broker_order_id, status=STATUS_REJECTED, error=str(exc)
            )

        return BrokerOrderResult(
            broker_order_id=result["id"],
            status=_normalize_alpaca_status(result.get("status")),
        )

    def cancel(self, broker_order_id: str) -> bool:
        try:
            return self._client.cancel_order(broker_order_id)
        except AlpacaNotConfiguredError:
            return False
