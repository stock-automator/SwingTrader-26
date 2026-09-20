"""`/api/v1/universe/*` - dynamic S&P 500 + Nasdaq-100 trading universe.

`backend.app.data.universe.UniverseManager` does the actual work (fetch,
sanitize, dedupe, denylist/broken-data filtering, persistence to
`config/universe.json`); this module only exposes it over HTTP.

`GET /symbols` triggers a sync on first read rather than 404ing when the
universe has never been synced - simpler for a fresh checkout (no
"call POST /sync once before GET ever works" step to document or forget),
and consistent with `check_and_schedule_startup_sync` already doing the same
thing on app startup. A `GET` call is idempotent either way: on every
subsequent call the file already exists and is returned as-is
(`needs_sync()` gates it here exactly like it gates the startup hook).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..data.universe import UniverseManager
from .schemas import UniverseSyncResponse

router = APIRouter(prefix="/api/v1/universe", tags=["universe"])


def _manager(settings: Settings) -> UniverseManager:
    return UniverseManager(max_workers=settings.screener_max_workers)


@router.post("/sync", response_model=UniverseSyncResponse)
async def sync_universe(settings: Settings = Depends(get_settings)) -> dict:
    """Runs `UniverseManager.sync()` - network/Wikipedia fetch + parquet
    scan - off the event loop via `asyncio.to_thread`, so this endpoint
    doesn't block other requests while it runs."""
    manager = _manager(settings)
    result = await asyncio.to_thread(manager.sync)
    return result.as_dict()


@router.get("/symbols", response_model=UniverseSyncResponse)
async def get_universe_symbols(settings: Settings = Depends(get_settings)) -> dict:
    """Current `config/universe.json` contents, syncing first if the
    universe has never been synced or is more than 24h stale."""
    manager = _manager(settings)
    if manager.needs_sync():
        result = await asyncio.to_thread(manager.sync)
        return result.as_dict()

    cached = manager.read()
    assert cached is not None  # needs_sync() was false, so a valid file exists
    return cached.as_dict()
