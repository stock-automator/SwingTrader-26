"""
`GET /api/v1/screener/live` and `WS /ws/screener` - the live setup screener.

Both share `_scan` so the WebSocket just re-runs the same REST logic on an
interval rather than carrying a second implementation of it.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from starlette.concurrency import run_in_threadpool

from ..config import Settings, get_settings
from ..data.loader import SPY_TICKER, DataUnavailableError, load_prices
from ..quant.risk import RiskManager
from ..quant.screener import RelativeStrengthScreener
from ..quant.setups import annotate_relative_strength, scan_universe
from ..quant.strategies import build_strategy
from .deps import load_frames, load_watchlist
from .schemas import ScreenerResponse

log = logging.getLogger(__name__)

router = APIRouter(tags=["screener"])

DEFAULT_STRATEGY = "donchian_breakout"
#: Notional account size the live screener sizes orders against - the same
#: product default as the backtest baseline, so a screener row's share count
#: is directly comparable to "what would a $1,000 account do here."
DEFAULT_SCREENER_EQUITY = 1000.0


def _scan(
    strategy_name: str,
    settings: Settings,
    account_equity: float,
    risk_per_trade_pct: float,
    tickers: list[str] | None = None,
) -> dict:
    try:
        strategy = build_strategy(strategy_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    universe = tickers or load_watchlist(settings)
    if not universe:
        return {"setups": [], "scanned": 0, "skipped": 0, "skip_reasons": {}}

    frames, warnings = load_frames(universe, settings)
    if not frames:
        raise HTTPException(
            status_code=503,
            detail="No usable price history for any watchlist ticker. "
            + "; ".join(warnings[:5]),
        )

    risk_manager = RiskManager(
        account_equity=account_equity, risk_per_trade_pct=risk_per_trade_pct
    )
    report = scan_universe(frames, strategy, risk_manager)

    try:
        spy_frame = load_prices(
            SPY_TICKER,
            data_dir=settings.data_dir,
            allow_download=settings.allow_downloads,
        )
        ranking = RelativeStrengthScreener(spy_frame).rank(frames)
        report.setups = annotate_relative_strength(report.setups, ranking)
    except (DataUnavailableError, ValueError) as exc:
        log.warning("relative-strength ranking skipped: %s", exc)

    payload = report.as_dict()
    payload["warnings"] = warnings
    return payload


@router.get("/api/v1/screener/live", response_model=ScreenerResponse)
def screener_live(
    strategy: str = Query(default=DEFAULT_STRATEGY),
    account_equity: float = Query(default=DEFAULT_SCREENER_EQUITY, gt=0),
    risk_per_trade_pct: float = Query(default=0.02, gt=0, le=1),
    tickers: str | None = Query(
        default=None, description="Comma-separated override for the watchlist"
    ),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Latest-bar long/short setups across the watchlist (or `tickers`)."""
    universe = (
        [t.strip().upper() for t in tickers.split(",") if t.strip()]
        if tickers
        else None
    )
    return _scan(strategy, settings, account_equity, risk_per_trade_pct, universe)


@router.websocket("/ws/screener")
async def screener_ws(
    websocket: WebSocket,
    strategy: str = Query(default=DEFAULT_STRATEGY),
    account_equity: float = Query(default=DEFAULT_SCREENER_EQUITY, gt=0),
    risk_per_trade_pct: float = Query(default=0.02, gt=0, le=1),
) -> None:
    """Push a fresh screener scan every `settings.ws_poll_seconds`.

    One scan runs immediately on connect, then on the configured interval
    until the client disconnects. Scan errors are sent as an `{"error":
    ...}` frame rather than closing the socket, so a single bad request
    (unknown strategy) doesn't require the client to reconnect.
    """
    await websocket.accept()
    settings = get_settings()

    try:
        while True:
            try:
                payload = await run_in_threadpool(
                    _scan, strategy, settings, account_equity, risk_per_trade_pct
                )
                await websocket.send_json(payload)
            except HTTPException as exc:
                await websocket.send_json({"error": exc.detail})

            await asyncio.sleep(settings.ws_poll_seconds)
    except WebSocketDisconnect:
        log.info("screener websocket client disconnected")
