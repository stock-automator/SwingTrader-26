"""`/api/v1/universe/*`: the S&P 500 + Nasdaq-100 trading universe.

`backend.app.data.universe.UniverseManager` does the work (fetch, diff,
history for new names, persistence to `Settings.universe_path`); this module
only exposes it over HTTP. The scheduler refreshes it monthly, so `GET
/symbols` only reads the file; it syncs only when no universe exists yet.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..data.universe import UniverseManager
from .schemas import UniverseSyncResponse

router = APIRouter(prefix="/api/v1/universe", tags=["universe"])


def universe_manager(settings: Settings) -> UniverseManager:
    """The manager for this app's universe, data and status files."""
    return UniverseManager(
        config_path=settings.universe_path,
        data_dir=settings.data_dir,
        max_workers=settings.screener_max_workers,
        status_path=settings.universe_status_path,
    )


@router.post("/sync", response_model=UniverseSyncResponse)
async def sync_universe(settings: Settings = Depends(get_settings)) -> dict:
    """Run `UniverseManager.sync()` off the event loop."""
    result = await asyncio.to_thread(universe_manager(settings).sync)
    return result.as_dict()


@router.get("/symbols", response_model=UniverseSyncResponse)
async def get_universe_symbols(settings: Settings = Depends(get_settings)) -> dict:
    """The stored universe; `changes` are those of the last sync."""
    manager = universe_manager(settings)
    cached = manager.read()
    if cached is None:
        cached = await asyncio.to_thread(manager.sync)
    return cached.as_dict()
