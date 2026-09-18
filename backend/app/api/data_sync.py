"""`POST /api/v1/data/sync` - background-executed Parquet cache refresh.

The sync itself (network fetches, corporate-action reconciliation, disk
writes) can take anywhere from seconds to minutes for a full watchlist, so
it runs via FastAPI's `BackgroundTasks` after the response is sent rather
than blocking the request - the endpoint answers immediately with which
tickers were queued, and `GET /sync/status` reports on progress/results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..config import Settings, get_settings
from ..quant.data.parquet_manager import ParquetSyncManager, SyncResult
from .deps import load_watchlist
from .schemas import DataSyncRequest, DataSyncResponse, DataSyncStatusResponse

router = APIRouter(prefix="/api/v1/data", tags=["data-sync"])


@dataclass
class _SyncJobState:
    """In-process record of the most recent sync run.

    A single-instance FastAPI process has nowhere else to durably track
    "is a sync running, what happened last time" without standing up a
    job queue / database table neither this feature nor the rest of the
    API currently has - this is deliberately the minimal thing that lets
    the frontend poll for progress, not a general-purpose task queue.
    """

    in_progress: bool = False
    started_at: str | None = None
    results: dict[str, SyncResult] = field(default_factory=dict)

    def start(self) -> None:
        self.in_progress = True
        self.started_at = datetime.now(timezone.utc).isoformat()

    def record(self, result: SyncResult) -> None:
        self.results[result.ticker] = result

    def finish(self) -> None:
        self.in_progress = False

    def as_dict(self) -> dict:
        return {
            "in_progress": self.in_progress,
            "started_at": self.started_at,
            "results": [r.as_dict() for r in self.results.values()],
        }


_job_state = _SyncJobState()


def _run_sync(tickers: list[str], data_dir: Path) -> None:
    """The background task body: sync every ticker, recording each result
    as it lands rather than waiting for the whole batch, so `/sync/status`
    reflects partial progress on a long run."""
    manager = ParquetSyncManager(data_dir=data_dir)
    try:
        for ticker in tickers:
            _job_state.record(manager.sync_ticker(ticker))
    finally:
        _job_state.finish()


@router.post("/sync", response_model=DataSyncResponse, status_code=202)
def start_data_sync(
    request: DataSyncRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Queue a staleness/corporate-action sync for `tickers` (or the full
    watchlist), and return immediately - the work happens after the
    response is sent."""
    tickers = request.tickers if request.tickers else load_watchlist(settings)
    if not tickers:
        raise HTTPException(
            status_code=422,
            detail="No tickers to sync: request body was empty and the watchlist is empty.",
        )

    _job_state.start()
    background_tasks.add_task(_run_sync, tickers, settings.data_dir)

    return {"status": "started", "tickers": tickers}


@router.get("/sync/status", response_model=DataSyncStatusResponse)
def data_sync_status() -> dict:
    """Progress/results of the most recently started sync run."""
    return _job_state.as_dict()
