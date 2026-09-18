"""
`/api/v1/backtest/{historical-date-scan,simulate-trade-execution}` -
point-in-time replay and fill simulation.

`historical-date-scan` answers "what would the screener have shown on date
X" - zero lookahead is structural, not conventional: `load_frames`'s `end`
parameter slices each ticker's frame at the source (`data.loader.
_slice_window`, already covered by its own tests) before anything sees it,
so `scan_universe`/`scan_ticker` - which only ever look at a frame's last
row - simply cannot see a bar after `target_date`.

`simulate-trade-execution` answers "what would this specific signal have
actually filled at" - the realistic-cost question `engine.run_backtest`
answers in aggregate over a whole backtest, here for one trade in isolation
so the point-in-time scan above can be chained into "and if I'd taken it."
"""

from __future__ import annotations

import math
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from ..config import Settings, get_settings
from ..data.loader import DataUnavailableError, fetch_earnings_dates, load_prices
from ..quant.engine import EXECUTION_MODE_NEXT_OPEN, estimate_atr_spread_pct
from ..quant.indicators import wilder_atr
from ..quant.risk import RiskManager
from ..quant.screener import CatalystFilter
from ..quant.setups import scan_universe
from ..quant.strategies import build_strategy
from .deps import load_frames, load_watchlist
from .schemas import (
    HistoricalDateScanRequest,
    HistoricalDateScanResponse,
    SimulateTradeExecutionRequest,
    SimulateTradeExecutionResponse,
)

router = APIRouter(prefix="/api/v1/backtest", tags=["replay"])


@router.post("/historical-date-scan", response_model=HistoricalDateScanResponse)
def historical_date_scan(
    request: HistoricalDateScanRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Runs `strategy` as of `target_date`, seeing only bars on or before it."""
    try:
        target_date = pd.Timestamp(request.target_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid target_date: {exc}"
        ) from exc
    if target_date > pd.Timestamp(datetime.now()):
        raise HTTPException(
            status_code=422, detail="target_date must not be in the future"
        )

    try:
        strategy = build_strategy(request.strategy, request.strategy_params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    universe = request.tickers or load_watchlist(settings)
    if not universe:
        return {
            "target_date": request.target_date,
            "setups": [],
            "scanned": 0,
            "skipped": 0,
            "skip_reasons": {},
            "warnings": [],
        }

    # `end=` is what makes this zero-lookahead: every frame is sliced to
    # bars on or before target_date before scan_universe ever sees it.
    frames, warnings = load_frames(universe, settings, end=request.target_date)
    if not frames:
        raise HTTPException(
            status_code=503,
            detail="No usable price history on or before "
            f"{request.target_date} for any requested ticker. "
            + "; ".join(warnings[:5]),
        )

    catalyst_filter = None
    earnings_by_ticker = None
    if request.earnings_blackout:
        catalyst_filter = CatalystFilter()
        earnings_by_ticker = {}
        for ticker in frames:
            try:
                earnings_by_ticker[ticker] = fetch_earnings_dates(ticker)
            except DataUnavailableError as exc:
                warnings.append(f"Earnings calendar unavailable for {ticker}: {exc}")

    risk_manager = RiskManager(
        account_equity=request.account_equity,
        risk_per_trade_pct=request.risk_per_trade_pct,
    )
    report = scan_universe(
        frames,
        strategy,
        risk_manager,
        catalyst_filter=catalyst_filter,
        earnings_by_ticker=earnings_by_ticker,
    )

    payload = report.as_dict()
    payload["target_date"] = request.target_date
    payload["warnings"] = warnings
    return payload


@router.post("/simulate-trade-execution", response_model=SimulateTradeExecutionResponse)
def simulate_trade_execution(
    request: SimulateTradeExecutionRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Simulates the realistic fill for one signal, at `entry_date`, under
    `execution_mode` - the same NEXT_OPEN/SAME_CLOSE_SLIPPAGE choice and
    spread model `engine.run_backtest` uses, applied to a single trade."""
    try:
        entry_date = pd.Timestamp(request.entry_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid entry_date: {exc}"
        ) from exc

    try:
        df = load_prices(
            request.ticker,
            data_dir=settings.data_dir,
            allow_download=settings.allow_downloads,
        )
    except DataUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if entry_date not in df.index:
        raise HTTPException(
            status_code=404,
            detail=f"No bar for {request.ticker} on {request.entry_date}",
        )

    idx = df.index.get_loc(entry_date)
    atr_series = wilder_atr(df)
    atr_at_entry = float(atr_series.iloc[idx])
    atr_value = atr_at_entry if math.isfinite(atr_at_entry) else None

    if request.execution_mode == EXECUTION_MODE_NEXT_OPEN:
        if idx + 1 >= len(df):
            raise HTTPException(
                status_code=422,
                detail=f"{request.entry_date} is the last available bar for "
                f"{request.ticker} - there is no next bar to fill NEXT_OPEN on.",
            )
        fill_bar_index = idx + 1
        reference_price = float(df["Open"].iloc[fill_bar_index])
    else:  # SAME_CLOSE_SLIPPAGE
        fill_bar_index = idx
        reference_price = float(df["Close"].iloc[idx])

    spread = (
        estimate_atr_spread_pct(df, request.atr_slippage_multiple)
        if request.atr_slippage_multiple > 0
        else request.slippage_pct
    )
    # Spread always moves the fill against the trader: worse (higher) for a
    # long entry, worse (lower) for a short entry - the same "you don't get
    # the quoted mid" cost `engine.run_backtest` prices in via `spread`.
    fill_price = (
        reference_price * (1 + spread)
        if request.direction == 1
        else reference_price * (1 - spread)
    )

    order = RiskManager(
        account_equity=request.account_equity,
        risk_per_trade_pct=request.risk_per_trade_pct,
    ).build_risk_managed_order(
        entry_price=fill_price,
        sl_type=request.sl_type,
        sl_value=request.sl_value,
        tp_type=request.tp_type,
        tp_value=request.tp_value,
        direction=request.direction,
        atr=atr_value,
    )

    notional_value = order.shares * order.entry_price
    slippage_cost = abs(order.entry_price - reference_price) * order.shares
    commission_cost = (
        request.fee_per_share * order.shares
        if request.fee_per_share > 0
        else request.commission * notional_value
    )

    return {
        "ticker": request.ticker,
        "execution_mode": request.execution_mode,
        "fill_date": df.index[fill_bar_index].strftime("%Y-%m-%d"),
        "fill_price": round(order.entry_price, 4),
        "reference_price": round(reference_price, 4),
        "slippage_pct_applied": spread,
        "slippage_cost": round(slippage_cost, 2),
        "commission_cost": round(commission_cost, 2),
        "total_cost": round(notional_value + commission_cost, 2),
        "shares": order.shares,
        "stop_loss": round(order.stop_loss, 4),
        "take_profit": round(order.take_profit, 4),
        "risk_amount": round(order.risk_amount, 2),
        "reward_risk_ratio": round(order.reward_risk_ratio, 2),
        "notional_value": round(notional_value, 2),
        "tradable": not order.rejected and order.shares > 0,
        "note": order.rejection_reason,
    }
