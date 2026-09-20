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
import threading
import time
from typing import Any

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

#: How long a computed regime report may be reused before recomputing.
#: Regime state changes on the order of minutes, not seconds, relative to
#: how often it's actually polled (the Dashboard badge, plus `WS
#: /ws/v1/live-feed`'s own `settings.ws_poll_seconds`-interval poll loop in
#: `api/ws.py`) - a short TTL lets concurrent/rapid callers (multiple
#: browser tabs, the WS loop overlapping a REST poll) share one breadth
#: scan across the whole cached universe instead of each recomputing it
#: independently. Deliberately shorter than the default `ws_poll_seconds`
#: (15s) so each WS tick still gets a mostly-fresh computation.
REGIME_CACHE_TTL_SECONDS = 10.0

#: Module-level: stateless and cheap to construct, but every request
#: reusing one instance avoids re-validating its (fixed) thresholds on
#: every poll.
_engine = MarketRegimeEngine()

_regime_cache_lock = threading.Lock()
_regime_cache: dict[str, Any] = {"report": None, "computed_at": 0.0}


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


def _compute_regime_report(settings: Settings) -> dict:
    spy_df = _safe_load_prices(SPY_TICKER, settings)
    qqq_df = _safe_load_prices(QQQ_TICKER, settings)
    vix_level = _latest_vix_level(settings)

    universe = cached_tickers(settings.data_dir)[:MAX_BREADTH_UNIVERSE]
    frames, _warnings = load_frames(universe, settings) if universe else ({}, [])
    breadth = _engine.compute_breadth(frames, max_workers=settings.screener_max_workers)

    report = _engine.classify(spy_df, qqq_df, vix_level, breadth)
    return report.as_dict()


def get_cached_regime_report(settings: Settings) -> dict:
    """`_compute_regime_report`, reused across calls within
    `REGIME_CACHE_TTL_SECONDS` - shared by `GET /regime` and `WS
    /ws/v1/live-feed`'s poll loop (`api/ws.py`) so both read paths hit one
    cache instead of each running their own full breadth scan."""
    now = time.monotonic()
    with _regime_cache_lock:
        cached_report = _regime_cache["report"]
        if (
            cached_report is not None
            and (now - _regime_cache["computed_at"]) < REGIME_CACHE_TTL_SECONDS
        ):
            return cached_report

    report = _compute_regime_report(settings)
    with _regime_cache_lock:
        _regime_cache["report"] = report
        _regime_cache["computed_at"] = time.monotonic()
    return report


def reset_regime_cache_for_tests() -> None:
    """Clears the cached regime report so the next call recomputes from
    scratch. Test-only - mirrors `db.session.reset_for_tests`; without it,
    tests that monkeypatch fresh SPY/QQQ/VIX/breadth inputs per-test would
    otherwise see a previous test's still-warm cached report."""
    with _regime_cache_lock:
        _regime_cache["report"] = None
        _regime_cache["computed_at"] = 0.0


@router.get("/regime", response_model=MarketRegimeResponse)
def get_market_regime(settings: Settings = Depends(get_settings)) -> dict:
    """SPY/QQQ EMA alignment + S&P 500 breadth + VIX regime -> one
    `MarketHealthReport`, for the Dashboard's traffic-light badge."""
    return get_cached_regime_report(settings)
