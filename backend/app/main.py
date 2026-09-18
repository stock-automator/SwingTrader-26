"""
FastAPI application entrypoint.

    uvicorn backend.app.main:app --reload --port 8000

Routes are split by concern into `api/backtest.py` (the $1,000 benchmark
engine) and `api/screener.py` (the live setup grid, REST + WebSocket) -
this module only wires them together with CORS and a health check.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.analytics import router as analytics_router
from .api.backtest import router as backtest_router
from .api.data_sync import router as data_sync_router
from .api.order_ticket import router as order_ticket_router
from .api.schemas import HealthResponse
from .api.screener import router as screener_router
from .config import get_settings

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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(backtest_router)
app.include_router(screener_router)
app.include_router(order_ticket_router)
app.include_router(data_sync_router)
app.include_router(analytics_router)


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "finnhub_configured": settings.has_finnhub,
        "allow_downloads": settings.allow_downloads,
    }
