"""
`GET /api/v1/screener/live` and `WS /ws/screener` - the live setup screener.

Both share `_scan` so the WebSocket just re-runs the same REST logic on an
interval rather than carrying a second implementation of it.
"""

from __future__ import annotations

import asyncio
import dataclasses
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
from ..data.loader import (
    SPY_TICKER,
    DataUnavailableError,
    fetch_earnings_dates,
    load_prices,
)
from ..quant.regime import MACRO_REGIME_UNKNOWN, MacroRegimeDetector
from ..quant.risk import CircuitBreaker, RiskManager
from ..quant.screener import CatalystFilter, RelativeStrengthScreener
from ..quant.setups import DIRECTION_LONG, annotate_relative_strength, scan_universe
from ..quant.strategies import build_strategy
from .deps import load_frames, load_watchlist, watchlist_overflow
from .schemas import ScreenerResponse

log = logging.getLogger(__name__)

router = APIRouter(tags=["screener"])

DEFAULT_STRATEGY = "donchian_breakout"
#: Notional account size the live screener sizes orders against - the same
#: product default as the backtest baseline, so a screener row's share count
#: is directly comparable to "what would a $1,000 account do here."
DEFAULT_SCREENER_EQUITY = 1000.0


def _suppress_longs(setups: list, note: str) -> list:
    """Downgrade every LONG row to non-tradable with an explanatory note,
    leaving SHORT/EXIT_LONG/FLAT rows untouched - used by both the macro
    regime gate and the drawdown circuit breaker below."""
    out = []
    for setup in setups:
        if setup.direction == DIRECTION_LONG and setup.tradable:
            out.append(dataclasses.replace(setup, tradable=False, note=note))
        else:
            out.append(setup)
    return out


def _scan(
    strategy_name: str,
    settings: Settings,
    account_equity: float,
    risk_per_trade_pct: float,
    tickers: list[str] | None = None,
    earnings_blackout: bool = False,
) -> dict:
    try:
        strategy = build_strategy(strategy_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    truncation_warning = None
    if tickers is None:
        universe = load_watchlist(settings)
        overflow = watchlist_overflow(settings)
        if overflow:
            truncation_warning = (
                f"Watchlist has {len(universe) + overflow} tickers; capped to "
                f"{settings.screener_max_tickers} (screener_max_tickers) - "
                f"{overflow} not scanned. Raise SCREENER_MAX_TICKERS to scan more."
            )
            log.warning(truncation_warning)
    else:
        universe = tickers

    if not universe:
        return {
            "setups": [],
            "scanned": 0,
            "skipped": 0,
            "skip_reasons": {},
            "macro_regime": MACRO_REGIME_UNKNOWN,
            "circuit_breaker_active": False,
            "warnings": [truncation_warning] if truncation_warning else [],
        }

    frames, warnings = load_frames(universe, settings)
    if truncation_warning:
        warnings.append(truncation_warning)
    if not frames:
        raise HTTPException(
            status_code=503,
            detail="No usable price history for any watchlist ticker. "
            + "; ".join(warnings[:5]),
        )

    catalyst_filter = None
    earnings_by_ticker = None
    if earnings_blackout:
        catalyst_filter = CatalystFilter()
        earnings_by_ticker = {}
        for ticker in frames:
            try:
                earnings_by_ticker[ticker] = fetch_earnings_dates(ticker)
            except DataUnavailableError as exc:
                log.warning("earnings calendar unavailable for %s: %s", ticker, exc)
                warnings.append(f"Earnings calendar unavailable for {ticker}: {exc}")

    risk_manager = RiskManager(
        account_equity=account_equity, risk_per_trade_pct=risk_per_trade_pct
    )
    report = scan_universe(
        frames,
        strategy,
        risk_manager,
        catalyst_filter=catalyst_filter,
        earnings_by_ticker=earnings_by_ticker,
        min_avg_volume=settings.screener_min_avg_volume,
        volume_lookback=settings.screener_volume_lookback,
        stale_after_days=settings.screener_stale_after_days,
    )

    spy_frame = None
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

    macro_regime = MACRO_REGIME_UNKNOWN
    circuit_breaker_active = False
    if spy_frame is not None:
        detector = MacroRegimeDetector()
        macro_regime = detector.current_regime(spy_frame)
        if detector.is_long_blocked(spy_frame):
            report.setups = _suppress_longs(
                report.setups,
                f"Macro regime {macro_regime} - new long setups suppressed.",
            )

        if len(spy_frame) > 1:
            circuit_breaker_active = CircuitBreaker().is_triggered(spy_frame["Close"])
            if circuit_breaker_active:
                report.setups = _suppress_longs(
                    report.setups,
                    "Circuit breaker active: SPY's rolling drawdown exceeds "
                    "the kill-switch threshold - new long setups halted.",
                )

    payload = report.as_dict()
    payload["warnings"] = warnings
    payload["macro_regime"] = macro_regime
    payload["circuit_breaker_active"] = circuit_breaker_active
    return payload


@router.get("/api/v1/screener/live", response_model=ScreenerResponse)
def screener_live(
    strategy: str = Query(default=DEFAULT_STRATEGY),
    account_equity: float = Query(default=DEFAULT_SCREENER_EQUITY, gt=0),
    risk_per_trade_pct: float = Query(default=0.02, gt=0, le=1),
    tickers: str | None = Query(
        default=None, description="Comma-separated override for the watchlist"
    ),
    earnings_blackout: bool = Query(
        default=False,
        description="Suppress long setups within 5 trading days of a known earnings date.",
    ),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Latest-bar long/short setups across the watchlist (or `tickers`)."""
    universe = (
        [t.strip().upper() for t in tickers.split(",") if t.strip()]
        if tickers
        else None
    )
    return _scan(
        strategy,
        settings,
        account_equity,
        risk_per_trade_pct,
        universe,
        earnings_blackout,
    )


@router.websocket("/ws/screener")
async def screener_ws(
    websocket: WebSocket,
    strategy: str = Query(default=DEFAULT_STRATEGY),
    account_equity: float = Query(default=DEFAULT_SCREENER_EQUITY, gt=0),
    risk_per_trade_pct: float = Query(default=0.02, gt=0, le=1),
    earnings_blackout: bool = Query(default=False),
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
                    _scan,
                    strategy,
                    settings,
                    account_equity,
                    risk_per_trade_pct,
                    None,
                    earnings_blackout,
                )
                await websocket.send_json(payload)
            except HTTPException as exc:
                await websocket.send_json({"error": exc.detail})

            await asyncio.sleep(settings.ws_poll_seconds)
    except WebSocketDisconnect:
        log.info("screener websocket client disconnected")
