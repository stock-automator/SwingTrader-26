"""`GET /api/v1/signals/live-today` - cross-strategy live signal matrix.

Runs every registered strategy (or a requested subset) over the watchlist
and flattens every actionable (LONG/SHORT) row into one grid tagged with
which strategy produced it - "what should I look at today," not the whole
scanned universe (FLAT/EXIT_LONG rows are dropped, same as the single-
strategy screener).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from ..analytics.expectancy import estimate_win_probability
from ..config import Settings, get_settings
from ..data.loader import (
    SPY_TICKER,
    DataUnavailableError,
    fetch_earnings_dates,
    load_prices,
)
from ..quant.risk import RiskManager
from ..quant.screener import CatalystFilter
from ..quant.setups import DIRECTION_LONG, DIRECTION_SHORT, scan_universe
from ..quant.strategies import REGISTRY, REQUIRES_BENCHMARK, build_strategy
from .deps import load_frames, load_watchlist, watchlist_overflow
from .schemas import SignalMatrixResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/signals", tags=["signals"])

_ACTIONABLE_DIRECTIONS = frozenset({DIRECTION_LONG, DIRECTION_SHORT})


@router.get("/live-today", response_model=SignalMatrixResponse)
def live_today(
    strategies: str | None = Query(
        default=None,
        description="Comma-separated subset of the strategy registry; omit for all.",
    ),
    tickers: str | None = Query(
        default=None, description="Comma-separated override for the watchlist"
    ),
    account_equity: float = Query(default=1000.0, gt=0),
    risk_per_trade_pct: float = Query(default=0.02, gt=0, le=1),
    earnings_blackout: bool = Query(default=False),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Every actionable setup across every requested strategy, for today."""
    if strategies:
        strategy_names = [s.strip() for s in strategies.split(",") if s.strip()]
        unknown = [s for s in strategy_names if s not in REGISTRY]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown strategies {unknown}. Available: {sorted(REGISTRY)}",
            )
    else:
        strategy_names = sorted(REGISTRY)

    warnings: list[str] = []

    if tickers:
        universe = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    else:
        universe = load_watchlist(settings)
        overflow = watchlist_overflow(settings)
        if overflow:
            warnings.append(
                f"Watchlist has {len(universe) + overflow} tickers; capped to "
                f"{settings.screener_max_tickers} (screener_max_tickers) - "
                f"{overflow} not scanned."
            )

    if not universe:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "rows": [],
            "scanned": 0,
            "warnings": warnings,
        }

    frames, load_warnings = load_frames(universe, settings)
    warnings.extend(load_warnings)
    if not frames:
        raise HTTPException(
            status_code=503,
            detail="No usable price history for any watchlist ticker. "
            + "; ".join(warnings[:5]),
        )

    spy_frame = None
    if any(name in REQUIRES_BENCHMARK for name in strategy_names):
        try:
            spy_frame = load_prices(
                SPY_TICKER,
                data_dir=settings.data_dir,
                allow_download=settings.allow_downloads,
            )
        except DataUnavailableError as exc:
            warnings.append(
                f"Benchmark unavailable, benchmark-dependent strategies skipped: {exc}"
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

    risk_manager = RiskManager(
        account_equity=account_equity, risk_per_trade_pct=risk_per_trade_pct
    )

    rows: list[dict] = []
    total_scanned = 0
    for name in strategy_names:
        if name in REQUIRES_BENCHMARK and spy_frame is None:
            warnings.append(f"{name}: requires a benchmark, none available - skipped.")
            continue

        try:
            strategy = build_strategy(name)
            if name in REQUIRES_BENCHMARK:
                strategy.set_benchmark(spy_frame)
        except ValueError as exc:
            warnings.append(f"{name}: {exc} - skipped.")
            continue

        try:
            report = scan_universe(
                frames,
                strategy,
                risk_manager,
                catalyst_filter=catalyst_filter,
                earnings_by_ticker=earnings_by_ticker,
            )
        except Exception as exc:  # a single bad strategy must not fail the grid
            log.warning("signal matrix: %s failed: %s", name, exc)
            warnings.append(f"{name}: {exc} - skipped.")
            continue

        total_scanned += len(report.setups) + report.skipped
        for setup in report.setups:
            if setup.direction not in _ACTIONABLE_DIRECTIONS:
                continue
            row = setup.as_dict()
            row["strategy"] = name
            if setup.direction == DIRECTION_LONG:
                estimate = estimate_win_probability(
                    strategy,
                    frames[setup.ticker],
                    risk_manager,
                    current_regime=setup.regime,
                )
                row.update(estimate.as_dict())
            else:
                # Short setups are screening-only (the backtest engine is
                # long-only) - no historical fills exist to estimate from.
                row["win_probability"] = None
            rows.append(row)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "scanned": total_scanned,
        "warnings": warnings,
    }
