"""
`WS /ws/v1/live-feed` - unified real-time push feed for the frontend's
`useWebSocket` hook: setup triggers, order fill/cancel notifications, and
market regime changes, multiplexed over one socket rather than three.

Follows the exact polling convention `api/screener.py`'s `/ws/screener`
already established: one round pushed immediately on connect, then every
`settings.ws_poll_seconds` until the client disconnects. Each frame is
tagged `{"type": ...}` so the client can route it without needing three
separate sockets:

    {"type": "regime", ...}         - MarketHealthReport (api/market.py)
    {"type": "signal", ...}         - one fresh screener scan (api/screener.py)
    {"type": "order_update", "orders": [...]}  - only orders that changed
                                       since the last push (api/orders.py)

A scan error (e.g. an unknown `strategy` query param) is sent as
`{"type": "error", ...}` rather than closing the socket - the same
"don't force a reconnect over one bad frame" posture `/ws/screener` takes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from ..config import Settings, get_settings
from .market import get_cached_regime_report
from .orders import list_orders_updated_since
from .screener import DEFAULT_SCREENER_EQUITY, DEFAULT_STRATEGY, _scan

log = logging.getLogger(__name__)

router = APIRouter(tags=["live-feed"])


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_regime_event(settings: Settings) -> dict[str, Any]:
    """Same `MarketHealthReport` `GET /api/v1/market/regime` returns, via
    `market.get_cached_regime_report` - shared with the REST endpoint's own
    cache rather than each poll tick running its own full breadth scan."""
    return {"type": "regime", **get_cached_regime_report(settings)}


def _build_signal_event(
    strategy: str,
    account_equity: float,
    risk_per_trade_pct: float,
    earnings_blackout: bool,
    settings: Settings,
) -> dict[str, Any]:
    payload = _scan(
        strategy, settings, account_equity, risk_per_trade_pct, None, earnings_blackout
    )
    return {"type": "signal", **payload}


def _build_order_update_event(since_iso: str) -> dict[str, Any] | None:
    orders = list_orders_updated_since(since_iso)
    if not orders:
        return None
    return {"type": "order_update", "orders": orders}


@router.websocket("/ws/v1/live-feed")
async def live_feed_ws(
    websocket: WebSocket,
    strategy: str = Query(default=DEFAULT_STRATEGY),
    account_equity: float = Query(default=DEFAULT_SCREENER_EQUITY, gt=0),
    risk_per_trade_pct: float = Query(default=0.02, gt=0, le=1),
    earnings_blackout: bool = Query(default=False),
) -> None:
    await websocket.accept()
    settings = get_settings()
    last_seen = _utcnow_iso()

    try:
        while True:
            regime_event = await run_in_threadpool(_build_regime_event, settings)
            await websocket.send_json(regime_event)

            try:
                signal_event = await run_in_threadpool(
                    _build_signal_event,
                    strategy,
                    account_equity,
                    risk_per_trade_pct,
                    earnings_blackout,
                    settings,
                )
                await websocket.send_json(signal_event)
            except HTTPException as exc:
                await websocket.send_json({"type": "error", "detail": exc.detail})

            next_seen = _utcnow_iso()
            order_event = await run_in_threadpool(_build_order_update_event, last_seen)
            if order_event is not None:
                await websocket.send_json(order_event)
            last_seen = next_seen

            await asyncio.sleep(settings.ws_poll_seconds)
    except WebSocketDisconnect:
        log.info("live-feed websocket client disconnected")
