"""
`/api/v1/orders/*` - broker-agnostic order routing engine.

Decoupled from `/api/v1/execution/*` (Alpaca-only, pre-existing): every
route here goes through `execution.broker.ExecutionBroker` - `PaperBroker`
by default, or `AlpacaBroker` when `{"broker": "ALPACA"}` is requested -
and persists the result to DuckDB's `routed_orders` table (`db/session.py`)
rather than in-process memory, so order state is thread-safe (the same
lock-guarded shared connection `api/scans.py` uses for scan jobs) and
survives a process restart.

Every broker call and DB write runs via `run_in_threadpool` so a slow
Alpaca API round-trip or a DuckDB write never blocks the async event loop -
the same non-blocking posture the WebSocket live-feed (`api/ws.py`) depends
on to keep pushing updates while an order is in flight.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..config import Settings, get_settings
from ..db.session import get_connection, init_db
from ..execution.alpaca_client import AlpacaExecutionClient
from ..execution.broker import (
    STATUS_CANCELLED,
    STATUS_PENDING,
    AlpacaBroker,
    BrokerOrderResult,
    ExecutionBroker,
    PaperBroker,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


class OrderSubmitRequest(BaseModel):
    """`POST /api/v1/orders/submit` body."""

    broker: str = Field(default="PAPER", description="'PAPER' or 'ALPACA'")
    ticker: str
    side: str = Field(description="'buy' or 'sell'")
    qty: float = Field(gt=0)
    order_type: str = Field(default="MARKET", description="'MARKET' or 'LIMIT'")
    limit_price: float | None = None
    reference_price: float | None = Field(
        default=None,
        description=(
            "Current quote to fill a PAPER MARKET order against - required "
            "for broker='PAPER' with order_type='MARKET'. Ignored by "
            "broker='ALPACA', which prices its own fill."
        ),
    )


class RoutedOrderResponse(BaseModel):
    order_id: str
    broker: str
    ticker: str
    side: str
    order_type: str
    qty: float
    limit_price: float | None
    status: str
    fill_price: float | None
    broker_order_id: str | None
    error: str | None
    submitted_at: str | None
    updated_at: str | None


class ActiveOrdersResponse(BaseModel):
    orders: list[RoutedOrderResponse]


_VALID_BROKERS = frozenset({"PAPER", "ALPACA"})


def _broker_for(name: str, settings: Settings) -> ExecutionBroker:
    if name == "ALPACA":
        return AlpacaBroker(
            AlpacaExecutionClient(settings.alpaca_api_key, settings.alpaca_api_secret)
        )
    return PaperBroker()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_response(row: tuple) -> dict[str, Any]:
    (
        order_id,
        broker,
        ticker,
        side,
        order_type,
        qty,
        limit_price,
        status,
        fill_price,
        broker_order_id,
        error,
        submitted_at,
        updated_at,
    ) = row
    return {
        "order_id": order_id,
        "broker": broker,
        "ticker": ticker,
        "side": side,
        "order_type": order_type,
        "qty": qty,
        "limit_price": limit_price,
        "status": status,
        "fill_price": fill_price,
        "broker_order_id": broker_order_id,
        "error": error,
        "submitted_at": str(submitted_at) if submitted_at is not None else None,
        "updated_at": str(updated_at) if updated_at is not None else None,
    }


_ORDER_COLUMNS = (
    "order_id, broker, ticker, side, order_type, qty, limit_price, "
    "status, fill_price, broker_order_id, error, submitted_at, updated_at"
)


def _insert_order(
    order_id: str,
    broker: str,
    ticker: str,
    side: str,
    order_type: str,
    qty: float,
    limit_price: float | None,
    result: BrokerOrderResult,
    submitted_at: str,
) -> None:
    init_db()
    with get_connection() as conn:
        conn.execute(
            f"INSERT INTO routed_orders ({_ORDER_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                order_id,
                broker,
                ticker.upper(),
                side.lower(),
                order_type,
                qty,
                limit_price,
                result.status,
                result.fill_price,
                result.broker_order_id,
                result.error,
                submitted_at,
                submitted_at,
            ],
        )


def _fetch_order(order_id: str) -> dict[str, Any] | None:
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {_ORDER_COLUMNS} FROM routed_orders WHERE order_id = ?",
            [order_id],
        ).fetchone()
    return _row_to_response(row) if row else None


def _fetch_active_orders() -> list[dict[str, Any]]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {_ORDER_COLUMNS} FROM routed_orders WHERE status = ? "
            "ORDER BY submitted_at DESC",
            [STATUS_PENDING],
        ).fetchall()
    return [_row_to_response(row) for row in rows]


def list_orders_updated_since(since_iso: str) -> list[dict[str, Any]]:
    """Every order whose `updated_at` is after `since_iso`.

    Used by the WebSocket live-feed (`api/ws.py`) to push fill/cancel
    notifications on each poll tick without re-sending every order's full
    state every time - only what actually changed since the last push.
    """
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {_ORDER_COLUMNS} FROM routed_orders WHERE updated_at > ? "
            "ORDER BY updated_at ASC",
            [since_iso],
        ).fetchall()
    return [_row_to_response(row) for row in rows]


def _mark_cancelled(order_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE routed_orders SET status = ?, updated_at = ? WHERE order_id = ?",
            [STATUS_CANCELLED, _utcnow_iso(), order_id],
        )


@router.post("/submit", response_model=RoutedOrderResponse)
async def submit_order(
    request: OrderSubmitRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Routes one order through `PaperBroker` or `AlpacaBroker` and
    persists the outcome - filled, rejected, or (Alpaca only) pending."""
    broker_name = request.broker.upper()
    if broker_name not in _VALID_BROKERS:
        raise HTTPException(
            status_code=422, detail=f"broker must be one of {sorted(_VALID_BROKERS)}"
        )
    if (
        broker_name == "PAPER"
        and request.order_type == "MARKET"
        and request.reference_price is None
    ):
        raise HTTPException(
            status_code=422,
            detail="reference_price is required for a PAPER MARKET order",
        )

    broker = _broker_for(broker_name, settings)
    order_id = str(uuid.uuid4())
    submitted_at = _utcnow_iso()

    try:
        result = await run_in_threadpool(
            broker.submit,
            request.ticker,
            request.side,
            request.qty,
            request.order_type,
            request.limit_price,
            request.reference_price,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await run_in_threadpool(
        _insert_order,
        order_id,
        broker_name,
        request.ticker,
        request.side,
        request.order_type,
        request.qty,
        request.limit_price,
        result,
        submitted_at,
    )
    return await run_in_threadpool(_fetch_order, order_id)


@router.post("/cancel/{order_id}", response_model=RoutedOrderResponse)
async def cancel_order(
    order_id: str, settings: Settings = Depends(get_settings)
) -> dict:
    """Cancels a still-pending order. 404 if the id is unknown, 422 if it
    isn't in a cancellable (`PENDING`) state or the broker declines."""
    row = await run_in_threadpool(_fetch_order, order_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    if row["status"] != STATUS_PENDING:
        raise HTTPException(
            status_code=422,
            detail=f"Order {order_id} is {row['status']}, not cancellable",
        )

    broker = _broker_for(row["broker"], settings)
    cancelled = await run_in_threadpool(broker.cancel, row["broker_order_id"])
    if not cancelled:
        raise HTTPException(
            status_code=422, detail=f"Broker declined to cancel order {order_id}"
        )

    await run_in_threadpool(_mark_cancelled, order_id)
    return await run_in_threadpool(_fetch_order, order_id)


@router.get("/active", response_model=ActiveOrdersResponse)
async def list_active_orders() -> dict:
    """Every order still in `PENDING` state - working orders awaiting a
    fill or cancel. `PaperBroker` fills synchronously, so only
    `AlpacaBroker` orders realistically ever land here."""
    orders = await run_in_threadpool(_fetch_active_orders)
    return {"orders": orders}
