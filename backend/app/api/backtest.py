"""`POST /api/v1/backtest` - the $1,000 relative-growth benchmark engine."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..config import Settings, get_settings
from ..data.loader import SPY_TICKER, DataUnavailableError, load_prices
from ..quant.backtest import comparison_to_payload, run_comparison
from ..quant.strategies import build_strategy
from .deps import load_frames
from .schemas import BacktestRequest, BacktestResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["backtest"])


@router.post("/backtest", response_model=BacktestResponse)
def run_backtest_endpoint(
    request: BacktestRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Run `strategy` over `tickers` and compare it to buy & hold and SPY,
    all three curves starting at the same `initial_capital` baseline.
    """
    tickers = request.tickers[: settings.max_backtest_tickers]

    try:
        strategy = build_strategy(request.strategy, request.strategy_params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    frames, warnings = load_frames(tickers, settings, request.start, request.end)
    if not frames:
        raise HTTPException(
            status_code=503,
            detail=f"No usable price history for any of {tickers}. "
            + "; ".join(warnings),
        )

    spy_frame = None
    try:
        spy_frame = load_prices(
            request.benchmark or SPY_TICKER,
            start=request.start,
            end=request.end,
            data_dir=settings.data_dir,
            allow_download=settings.allow_downloads,
        )
    except DataUnavailableError as exc:
        log.warning("SPY benchmark unavailable: %s", exc)
        warnings.append(f"Benchmark {request.benchmark}: {exc}")

    try:
        comparison = run_comparison(
            strategy,
            frames,
            spy_frame=spy_frame,
            initial_capital=request.initial_capital,
            risk_per_trade_pct=request.risk_per_trade_pct,
            commission=request.commission,
            slippage_pct=request.slippage_pct,
            risk_free_rate=request.risk_free_rate,
            include_buy_and_hold=request.include_buy_and_hold,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload = comparison_to_payload(comparison)
    payload["warnings"] = [*warnings, *payload["warnings"]]
    return payload
