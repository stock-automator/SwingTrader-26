"""
FastAPI application entrypoint.

    uvicorn backend.app.main:app --reload --port 8000
    # For mobile/LAN/NordVPN Meshnet access, bind every interface:
    uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

Routes are split by concern into `api/backtest.py` (the $1,000 benchmark
engine) and `api/screener.py` (the live setup grid, REST + WebSocket) -
this module only wires them together with CORS and a health check.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.alerts import router as alerts_router
from .api.analytics import router as analytics_router
from .api.backtest import router as backtest_router
from .api.data_sync import router as data_sync_router
from .api.execution import router as execution_router
from .api.journal import router as journal_router
from .api.market import router as market_router
from .api.order_ticket import router as order_ticket_router
from .api.orders import router as orders_router
from .api.replay import router as replay_router
from .api.risk import router as risk_router
from .api.scans import router as scans_router
from .api.schemas import HealthResponse
from .api.screener import router as screener_router
from .api.signals import router as signals_router
from .api.universe import router as universe_router
from .api.ws import router as live_feed_router
from .config import get_settings
from .data.universe import register_universe_startup

app = FastAPI(
    title="SwingTrader API",
    description=(
        "Quantitative swing & momentum trading platform: strategy "
        "backtesting benchmarked against a $1,000 baseline and SPY, plus a "
        "live setup screener."
    ),
    version="1.0.0",
)

_settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_settings.cors_origins),
    # Matches the Vite dev server reached from a phone/laptop on the same
    # LAN or NordVPN/Tailscale Meshnet - see `Settings.cors_origin_regex`.
    # `None` (e.g. `CORS_ORIGIN_REGEX=` set empty) disables it entirely.
    allow_origin_regex=_settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(backtest_router)
app.include_router(screener_router)
app.include_router(order_ticket_router)
app.include_router(data_sync_router)
app.include_router(analytics_router)
app.include_router(alerts_router)
app.include_router(execution_router)
app.include_router(signals_router)
app.include_router(replay_router)
app.include_router(journal_router)
app.include_router(scans_router)
app.include_router(universe_router)
app.include_router(market_router)
app.include_router(risk_router)
app.include_router(orders_router)
app.include_router(live_feed_router)

register_universe_startup(app)


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "finnhub_configured": settings.has_finnhub,
        "allow_downloads": settings.allow_downloads,
    }
