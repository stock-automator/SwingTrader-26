"""`/api/v1/execution/*` - Alpaca paper-trading order placement.

Every route here 503s with a clear message if `ALPACA_API_KEY`/
`ALPACA_API_SECRET` aren't set, rather than silently no-opping - a caller
must not be able to mistake "not configured" for "order placed". The
underlying SDK client is always paper (`execution/alpaca_client.py`
hardcodes `paper=True`); there is no setting anywhere that makes this
endpoint place a live order.

`submit_order` also runs `execution.guards.check_order_guards` first (unless
`Settings.execution_guards_enabled` is off): a closed/illiquid-session
dispatch or one landing inside an earnings/split lockout window is rejected
with a 422 rather than sent to Alpaca, regardless of whether the account is
paper or (in the future) live.
"""

from __future__ import annotations

import logging

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from ..config import Settings, get_settings
from ..data.loader import DataUnavailableError, fetch_earnings_dates, fetch_stock_splits
from ..execution.alpaca_client import AlpacaExecutionClient, AlpacaNotConfiguredError
from ..execution.guards import (
    EarningsLockoutGuard,
    MarketSessionGuard,
    check_order_guards,
)
from .schemas import (
    AlpacaAccountResponse,
    CloseAllPositionsRequest,
    CloseAllPositionsResponse,
    ExecutionOrderRequest,
    ExecutionOrderResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/execution", tags=["execution"])


def _client(settings: Settings = Depends(get_settings)) -> AlpacaExecutionClient:
    return AlpacaExecutionClient(settings.alpaca_api_key, settings.alpaca_api_secret)


def _require_configured(client: AlpacaExecutionClient) -> None:
    if not client.is_configured:
        raise HTTPException(
            status_code=503,
            detail="Alpaca is not configured: set ALPACA_API_KEY and "
            "ALPACA_API_SECRET to place paper-trading orders.",
        )


def _current_moment() -> pd.Timestamp:
    """Wall-clock "now", Eastern - its own function so tests can monkeypatch
    a fixed moment instead of depending on whatever time the suite runs at."""
    return pd.Timestamp.now(tz="America/New_York")


def _enforce_dispatch_guards(ticker: str, settings: Settings) -> None:
    """Raises 422 if `execution.guards.check_order_guards` rejects dispatching
    `ticker` right now. No-ops entirely if guards are disabled via settings."""
    if not settings.execution_guards_enabled:
        return

    earnings_dates: list = []
    split_dates: list = []
    try:
        earnings_dates = fetch_earnings_dates(ticker)
    except DataUnavailableError as exc:
        log.warning(
            "execution guard: earnings calendar unavailable for %s: %s", ticker, exc
        )
    try:
        split_dates = fetch_stock_splits(ticker)
    except DataUnavailableError as exc:
        log.warning(
            "execution guard: split history unavailable for %s: %s", ticker, exc
        )

    result = check_order_guards(
        _current_moment(),
        earnings_dates=earnings_dates,
        split_dates=split_dates,
        session_guard=MarketSessionGuard(
            allow_extended_hours=settings.execution_allow_extended_hours
        ),
        earnings_guard=EarningsLockoutGuard(
            lockout_hours=settings.earnings_lockout_hours
        ),
    )
    if not result.allowed:
        raise HTTPException(
            status_code=422,
            detail="Order dispatch blocked: " + "; ".join(result.reasons),
        )


@router.post("/orders", response_model=ExecutionOrderResponse)
def submit_order(
    request: ExecutionOrderRequest,
    client: AlpacaExecutionClient = Depends(_client),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Places a MARKET, LIMIT, or BRACKET paper order."""
    _require_configured(client)
    _enforce_dispatch_guards(request.ticker, settings)

    try:
        if request.order_type == "MARKET":
            return client.submit_market_order(request.ticker, request.qty, request.side)
        if request.order_type == "LIMIT":
            if request.limit_price is None:
                raise HTTPException(
                    status_code=422, detail="limit_price is required for a LIMIT order"
                )
            return client.submit_limit_order(
                request.ticker, request.qty, request.side, request.limit_price
            )
        # BRACKET
        if request.stop_loss is None or request.take_profit is None:
            raise HTTPException(
                status_code=422,
                detail="stop_loss and take_profit are required for a BRACKET order",
            )
        return client.submit_bracket_order(
            request.ticker,
            request.qty,
            request.side,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            limit_price=request.limit_price,
        )
    except AlpacaNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/close-all", response_model=CloseAllPositionsResponse)
def close_all_positions(
    request: CloseAllPositionsRequest,
    client: AlpacaExecutionClient = Depends(_client),
) -> dict:
    """Emergency kill-switch: liquidates every open paper position and
    cancels every open order. Requires `{"confirm": true}` - this is a
    destructive, account-wide action even on a paper account.

    Deliberately does not run `_enforce_dispatch_guards`: the session/
    earnings guards exist to stop new risk from going *on*, and must never
    be able to block an emergency de-risking action from coming *off*."""
    if not request.confirm:
        raise HTTPException(
            status_code=422,
            detail="Set confirm=true to close all positions - this liquidates "
            "the entire paper account.",
        )
    _require_configured(client)

    try:
        return {"closed": client.close_all_positions()}
    except AlpacaNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/account", response_model=AlpacaAccountResponse)
def get_account(client: AlpacaExecutionClient = Depends(_client)) -> dict:
    """Paper account equity/cash/buying-power snapshot."""
    _require_configured(client)

    try:
        return client.get_account()
    except AlpacaNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
