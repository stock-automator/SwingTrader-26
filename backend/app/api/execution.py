"""`/api/v1/execution/*` - Alpaca paper-trading order placement.

Every route here 503s with a clear message if `ALPACA_API_KEY`/
`ALPACA_API_SECRET` aren't set, rather than silently no-opping - a caller
must not be able to mistake "not configured" for "order placed". The
underlying SDK client is always paper (`execution/alpaca_client.py`
hardcodes `paper=True`); there is no setting anywhere that makes this
endpoint place a live order.

`submit_order` always runs `execution.guards.enforce_order_guards` first: a
new ENTRY outside the regular session (per Alpaca's calendar), inside an
earnings/split lockout, or whose calendar lookup failed is rejected with a
422 and never sent to Alpaca. An EXIT always goes through.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from ..config import Settings, get_settings
from ..execution.alpaca_client import AlpacaExecutionClient, AlpacaNotConfiguredError
from ..execution.guards import enforce_order_guards, intent_for
from ..execution.market_clock import MarketClock, get_market_clock
from .schemas import (
    AlpacaAccountResponse,
    CloseAllPositionsRequest,
    CloseAllPositionsResponse,
    ExecutionOrderRequest,
    ExecutionOrderResponse,
    PortfolioHistoryResponse,
    PositionsResponse,
)

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


def raise_if_blocked(
    ticker: str,
    side: str,
    qty: float,
    intent: str | None,
    held_positions: Callable[[], list[dict]] | None,
    settings: Settings,
    clock: MarketClock,
) -> None:
    """422 with every reason if `enforce_order_guards` blocks this order.
    Shared by both order routes (`/execution/orders`, `/orders/submit`).
    An explicit `intent="ENTRY"` always guards; `"EXIT"` is only honoured
    when `intent_for` proves it from `held_positions`."""
    resolved = (
        "ENTRY" if intent == "ENTRY" else intent_for(ticker, side, qty, held_positions)
    )
    result = enforce_order_guards(ticker, resolved, settings, clock)
    if not result.allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Order blocked ({resolved}): " + " ".join(result.reasons),
        )


@router.post("/orders", response_model=ExecutionOrderResponse)
def submit_order(
    request: ExecutionOrderRequest,
    client: AlpacaExecutionClient = Depends(_client),
    settings: Settings = Depends(get_settings),
    clock: MarketClock = Depends(get_market_clock),
) -> dict:
    """Places a MARKET, LIMIT, or BRACKET paper order."""
    _require_configured(client)
    raise_if_blocked(
        request.ticker,
        request.side,
        request.qty,
        request.intent,
        client.get_positions,
        settings,
        clock,
    )

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
            client_order_id=request.client_order_id,
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

    Deliberately does not run the order guards: the session/
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


@router.get("/positions", response_model=PositionsResponse)
def get_positions(client: AlpacaExecutionClient = Depends(_client)) -> dict:
    """Every currently open paper position."""
    _require_configured(client)

    try:
        return {"positions": client.get_positions()}
    except AlpacaNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/portfolio-history", response_model=PortfolioHistoryResponse)
def get_portfolio_history(
    period: str = "1M",
    timeframe: str = "1D",
    client: AlpacaExecutionClient = Depends(_client),
) -> dict:
    """Paper account equity curve for the Portfolio Dashboard's chart."""
    _require_configured(client)

    try:
        return client.get_portfolio_history(period=period, timeframe=timeframe)
    except AlpacaNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
