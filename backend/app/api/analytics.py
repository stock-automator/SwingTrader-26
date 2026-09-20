"""
Phase 3 quant analytics: `/api/v1/analytics/{monte-carlo,walk-forward,
factor-exposure}`.

Every route here can run dozens of backtests in one request (a Monte Carlo
run's trade collection, a walk-forward scan's per-window backtests, a
parameter-sensitivity sweep's per-value backtests) - real CPU time, not
I/O. `run_in_threadpool` moves that work off the event loop so it doesn't
stall other requests being served by the same process, the same pattern
`api/screener.py`'s WebSocket handler already uses for its own per-scan
backtests.

Every route here is also `async def`, unlike `api/backtest.py`'s plain
`def` handler (which Starlette auto-threadpools for free) - so `load_frames`
and `_load_benchmark_or_503` (both synchronous: parquet reads, and a
possible live yfinance fetch on a cache miss) are explicitly wrapped in
`run_in_threadpool` too, not just the backtest/analytics computation that
follows them. Without that, the event loop would still stall for the full
data-loading duration before ever reaching the already-threadpooled work.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from ..config import Settings, get_settings
from ..data.loader import SPY_TICKER, DataUnavailableError, load_prices
from ..quant.analytics import compute_factor_exposure
from ..quant.backtest import SPY_KEY, STRATEGY_KEY, run_comparison
from ..quant.engine import run_backtest
from ..quant.monte_carlo import run_monte_carlo
from ..quant.risk import RiskManager
from ..quant.strategies import REQUIRES_BENCHMARK, build_strategy
from ..quant.walk_forward import (
    DEFAULT_PARAMETER_PERTURBATIONS,
    analyze_parameter_sensitivity,
    run_walk_forward,
)
from .deps import load_frames
from .schemas import (
    FactorExposureRequest,
    FactorExposureResponse,
    MonteCarloRequest,
    MonteCarloResponse,
    WalkForwardRequest,
    WalkForwardResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def _build_strategy_or_422(strategy_name: str, strategy_params: dict):
    try:
        return build_strategy(strategy_name, strategy_params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _load_benchmark_or_503(settings: Settings, benchmark: str, start, end):
    try:
        return load_prices(
            benchmark,
            start=start,
            end=end,
            data_dir=settings.data_dir,
            allow_download=settings.allow_downloads,
        )
    except DataUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail=f"Benchmark {benchmark} unavailable: {exc}"
        ) from exc


@router.post("/monte-carlo", response_model=MonteCarloResponse)
async def monte_carlo_endpoint(
    request: MonteCarloRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Bootstrap the trade P&Ls `strategy` produced over `tickers` into
    `n_simulations` alternate equity paths (Risk of Ruin, drawdown
    percentile bands, equity distribution)."""
    strategy = _build_strategy_or_422(request.strategy, request.strategy_params)

    tickers = request.tickers[: settings.max_backtest_tickers]
    frames, warnings = await run_in_threadpool(
        load_frames, tickers, settings, request.start, request.end
    )
    if not frames:
        raise HTTPException(
            status_code=503,
            detail=f"No usable price history for any of {tickers}. "
            + "; ".join(warnings),
        )

    if request.strategy in REQUIRES_BENCHMARK:
        spy_frame = await run_in_threadpool(
            _load_benchmark_or_503, settings, SPY_TICKER, request.start, request.end
        )
        strategy.set_benchmark(spy_frame)

    def _collect_trade_pnls() -> list[float]:
        sleeve_capital = request.initial_capital / len(frames)
        pnls: list[float] = []
        for df in frames.values():
            risk_manager = RiskManager(
                account_equity=sleeve_capital,
                risk_per_trade_pct=request.risk_per_trade_pct,
            )
            result = run_backtest(
                strategy,
                df,
                risk_manager,
                commission=request.commission,
                slippage_pct=request.slippage_pct,
            )
            pnls.extend(float(v) for v in result.trades["pnl"].tolist())
        return pnls

    trade_pnls = await run_in_threadpool(_collect_trade_pnls)
    if not trade_pnls:
        raise HTTPException(
            status_code=422,
            detail="Strategy produced no closed trades over this window - nothing to simulate.",
        )

    mc_result = await run_in_threadpool(
        run_monte_carlo,
        trade_pnls,
        request.initial_capital,
        request.n_simulations,
        request.with_replacement,
        request.ruin_threshold_pct,
        request.seed,
    )

    payload = mc_result.as_dict()
    payload["warnings"] = warnings
    return payload


@router.post("/walk-forward", response_model=WalkForwardResponse)
async def walk_forward_endpoint(
    request: WalkForwardRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Rolling in-sample/out-of-sample scan for `strategy` on `ticker`,
    plus (when `param_name` is supplied) a +/-20% parameter-sensitivity
    sweep around that parameter's current value."""
    strategy = _build_strategy_or_422(request.strategy, request.strategy_params)

    frames, warnings = await run_in_threadpool(
        load_frames, [request.ticker], settings, request.start, request.end
    )
    if request.ticker not in frames:
        raise HTTPException(
            status_code=503,
            detail=f"No usable price history for {request.ticker}. "
            + "; ".join(warnings),
        )
    df = frames[request.ticker]

    if request.strategy in REQUIRES_BENCHMARK:
        spy_frame = await run_in_threadpool(
            _load_benchmark_or_503, settings, SPY_TICKER, request.start, request.end
        )
        strategy.set_benchmark(spy_frame)

    wf_result = await run_in_threadpool(
        run_walk_forward,
        strategy,
        df,
        request.is_months,
        request.oos_months,
        request.step_months,
        request.initial_capital,
        request.risk_per_trade_pct,
        request.commission,
        request.slippage_pct,
    )

    payload = wf_result.as_dict()
    payload["ticker"] = request.ticker
    payload["warnings"] = warnings
    payload["parameter_sensitivity"] = None

    if request.param_name:
        if request.param_name not in request.strategy_params:
            raise HTTPException(
                status_code=422,
                detail=f"param_name {request.param_name!r} must be a key in strategy_params.",
            )
        baseline_value = float(request.strategy_params[request.param_name])
        cast = int if request.param_type == "int" else float
        param_name = request.param_name
        base_params = request.strategy_params
        strategy_name = request.strategy

        def _factory(value: float):
            params = {**base_params, param_name: cast(round(value))}
            return build_strategy(strategy_name, params)

        perturbations = request.perturbation_pcts or list(
            DEFAULT_PARAMETER_PERTURBATIONS
        )
        sensitivity = await run_in_threadpool(
            analyze_parameter_sensitivity,
            _factory,
            param_name,
            baseline_value,
            df,
            perturbations,
            request.initial_capital,
            request.risk_per_trade_pct,
            request.commission,
            request.slippage_pct,
        )
        payload["parameter_sensitivity"] = sensitivity.as_dict()

    return payload


@router.post("/factor-exposure", response_model=FactorExposureResponse)
async def factor_exposure_endpoint(
    request: FactorExposureRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Alpha/Beta (vs. `benchmark`), Sharpe, Sortino, Calmar, and Tail
    Ratio for `strategy` over `tickers`."""
    strategy = _build_strategy_or_422(request.strategy, request.strategy_params)

    tickers = request.tickers[: settings.max_backtest_tickers]
    frames, warnings = await run_in_threadpool(
        load_frames, tickers, settings, request.start, request.end
    )
    if not frames:
        raise HTTPException(
            status_code=503,
            detail=f"No usable price history for any of {tickers}. "
            + "; ".join(warnings),
        )

    spy_frame = await run_in_threadpool(
        _load_benchmark_or_503, settings, request.benchmark, request.start, request.end
    )
    if request.strategy in REQUIRES_BENCHMARK:
        strategy.set_benchmark(spy_frame)

    def _run():
        comparison = run_comparison(
            strategy,
            frames,
            spy_frame=spy_frame,
            initial_capital=request.initial_capital,
            risk_per_trade_pct=request.risk_per_trade_pct,
            commission=request.commission,
            slippage_pct=request.slippage_pct,
            risk_free_rate=request.risk_free_rate,
            include_buy_and_hold=False,
        )
        strategy_returns = comparison.curves[STRATEGY_KEY].pct_change().dropna()
        benchmark_returns = comparison.curves[SPY_KEY].pct_change().dropna()
        return compute_factor_exposure(
            strategy_returns, benchmark_returns, risk_free_rate=request.risk_free_rate
        )

    try:
        factor_exposure = await run_in_threadpool(_run)
    except RuntimeError as exc:  # quantstats not installed
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload = factor_exposure.as_dict()
    payload["warnings"] = warnings
    return payload
