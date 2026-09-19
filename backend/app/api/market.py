"""
`GET /api/v1/market/regime` - top-down market health "traffic light".

Resolves SPY/QQQ bars, the VIX's latest close, and a breadth universe (every
ticker in the parquet cache, capped at `MAX_BREADTH_UNIVERSE`) via the same
`data.loader`/`api.deps` plumbing every other route uses, then hands them to
`quant.regime.MarketRegimeEngine` - the pure classification logic lives
there so it can be unit tested without HTTP or the network.

Any one input being unavailable (no network, an uncached VIX, an empty
universe) degrades that piece to `None`/`UNKNOWN` rather than 500ing the
whole endpoint - the same "unscoreable is not blocked" posture
`MacroRegimeDetector`/`execution.guards.EarningsLockoutGuard` already take,
consistent with a top-level status badge that should always render
*something* rather than go blank.
"""

from __future__ import annotations

import logging

import pandas as pd
from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..data.loader import DataUnavailableError, cached_tickers, load_prices
from ..quant.regime import MarketRegimeEngine
from .deps import load_frames
from .schemas import MarketRegimeResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/market", tags=["market"])

SPY_TICKER = "SPY"
QQQ_TICKER = "QQQ"
VIX_TICKER = "^VIX"

#: Breadth universe cap - the full parquet cache can run into the high
#: hundreds; capped so one dashboard poll can't turn into an unbounded
#: thread-pool fan-out (see `quant.regime.MarketRegimeEngine.compute_breadth`).
MAX_BREADTH_UNIVERSE = 500

#: Module-level: stateless and cheap to construct, but every request
#: reusing one instance avoids re-validating its (fixed) thresholds on
#: every poll.
_engine = MarketRegimeEngine()


def _safe_load_prices(ticker: str, settings: Settings) -> pd.DataFrame | None:
    """`load_prices`, degrading an unavailable ticker to `None` rather than
    raising - a single missing index (no network, an uncached VIX in a dev
    sandbox) should not 500 the whole regime endpoint."""
    try:
        return load_prices(
            ticker, data_dir=settings.data_dir, allow_download=settings.allow_downloads
        )
    except DataUnavailableError as exc:
        log.warning("market regime: %s unavailable: %s", ticker, exc)
        return None


def _latest_vix_level(settings: Settings) -> float | None:
    vix_df = _safe_load_prices(VIX_TICKER, settings)
    if vix_df is None or vix_df.empty:
        return None
    return float(vix_df["Close"].iloc[-1])


@router.get("/regime", response_model=MarketRegimeResponse)
def get_market_regime(settings: Settings = Depends(get_settings)) -> dict:
    """SPY/QQQ EMA alignment + S&P 500 breadth + VIX regime -> one
    `MarketHealthReport`, for the Dashboard's traffic-light badge."""
    spy_df = _safe_load_prices(SPY_TICKER, settings)
    qqq_df = _safe_load_prices(QQQ_TICKER, settings)
    vix_level = _latest_vix_level(settings)

    universe = cached_tickers(settings.data_dir)[:MAX_BREADTH_UNIVERSE]
    frames, _warnings = load_frames(universe, settings) if universe else ({}, [])
    breadth = _engine.compute_breadth(frames, max_workers=settings.screener_max_workers)

    report = _engine.classify(spy_df, qqq_df, vix_level, breadth)
    return report.as_dict()
