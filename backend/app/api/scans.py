"""
`POST/GET /api/v1/scans` - asynchronous, all-strategy background scanning,
persisted to DuckDB (`backend/app/db/session.py`) so a job's results survive
past the request/response cycle and can be polled or listed later.

Deliberately a separate router from `screener.py` rather than a rewrite of
it: `GET /api/v1/screener/live` and `WS /ws/screener` stay exactly as they
were (synchronous, single-strategy, in-memory) for backwards compatibility.
This module reuses `screener._scan` unchanged - one call per requested
strategy - and layers a job id, background execution, and durable storage
on top.

Column mapping note (`scan_results`): the spec's DDL includes
`win_probability` and `trigger_reason` columns, but nothing in
`quant.setups.Setup` currently computes a win probability - it is persisted
as `NULL` rather than fabricated. `trigger_reason` falls back to a
synthesized `"<strategy> <regime> signal"` string when a setup has no
`note` of its own (e.g. a macro-regime/circuit-breaker suppression). The
full `Setup.as_dict()` is always kept verbatim in `payload` so no
information is lost to the narrower typed columns.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..config import Settings, get_settings
from ..db.session import get_connection, init_db
from ..quant.strategies import REGISTRY
from .screener import DEFAULT_SCREENER_EQUITY, _scan

log = logging.getLogger(__name__)

router = APIRouter(tags=["scans"])

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

#: `GET /api/v1/scans` listing cap - a dashboard needs the latest handful of
#: jobs, not the full history.
MAX_JOBS_LISTED = 50


class ScanRequest(BaseModel):
    """`POST /api/v1/scans` body."""

    strategies: list[str] | None = Field(
        default=None,
        description="Strategy ids to scan. Omit to scan every registered strategy.",
    )
    tickers: list[str] | None = Field(
        default=None, description="Override for the watchlist universe."
    )
    account_equity: float = Field(default=DEFAULT_SCREENER_EQUITY, gt=0)
    risk_per_trade_pct: float = Field(default=0.02, gt=0, le=1)
    earnings_blackout: bool = Field(default=False)


class ScanJobCreated(BaseModel):
    job_id: str


class ScanResultRow(BaseModel):
    ticker: str
    strategy: str
    direction: str
    entry: float | None
    stop: float | None
    target: float | None
    win_probability: float | None
    r_multiple: float | None
    trigger_reason: str | None
    payload: dict[str, Any]


class ScanJobStatus(BaseModel):
    job_id: str
    status: str
    params: dict[str, Any]
    created_at: str | None
    completed_at: str | None
    error: str | None
    results: list[ScanResultRow] | None = None


class ScanJobSummary(BaseModel):
    job_id: str
    status: str
    created_at: str | None
    completed_at: str | None
    error: str | None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stringify_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _as_json_obj(value: Any) -> dict[str, Any]:
    """DuckDB's JSON columns round-trip as either a `str` (the raw JSON
    text) or an already-parsed Python object depending on client/version -
    normalize either shape to a `dict`."""
    if value is None:
        return {}
    if isinstance(value, str):
        parsed: Any = json.loads(value)
        return dict(parsed) if isinstance(parsed, dict) else {}
    return dict(value)


def _create_job(job_id: str, params: dict[str, Any]) -> None:
    init_db()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO scan_jobs "
            "(job_id, status, params, created_at, completed_at, error) "
            "VALUES (?, ?, ?, ?, NULL, NULL)",
            [job_id, STATUS_PENDING, json.dumps(params), _utcnow_iso()],
        )


def _set_job_status(
    job_id: str,
    status: str,
    *,
    completed_at: str | None = None,
    error: str | None = None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE scan_jobs SET status = ?, completed_at = ?, error = ? "
            "WHERE job_id = ?",
            [status, completed_at, error, job_id],
        )


def _setup_to_row(
    job_id: str, strategy_name: str, setup: dict[str, Any]
) -> tuple[Any, ...]:
    trigger_reason = setup.get("note") or (
        f"{strategy_name} {setup.get('regime', 'UNKNOWN')} signal"
    )
    return (
        job_id,
        setup.get("ticker"),
        strategy_name,
        setup.get("direction"),
        setup.get("entry_price"),
        setup.get("stop_loss"),
        setup.get("take_profit"),
        None,  # win_probability - not produced upstream; see module docstring.
        setup.get("reward_risk_ratio"),
        trigger_reason,
        json.dumps(setup),
    )


def _persist_results(job_id: str, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    with get_connection() as conn:
        conn.executemany(
            "INSERT INTO scan_results "
            "(job_id, ticker, strategy, direction, entry, stop, target, "
            "win_probability, r_multiple, trigger_reason, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _run_scan_job(
    job_id: str,
    strategies: list[str],
    settings: Settings,
    account_equity: float,
    risk_per_trade_pct: float,
    tickers: list[str] | None,
    earnings_blackout: bool,
) -> None:
    """Runs off the event loop - dispatched via `BackgroundTasks`, which
    Starlette hands off to its threadpool for a synchronous callable like
    this one, so the scan's CPU-bound work never blocks the server."""
    _set_job_status(job_id, STATUS_RUNNING)
    try:
        rows: list[tuple[Any, ...]] = []
        for strategy_name in strategies:
            payload = _scan(
                strategy_name,
                settings,
                account_equity,
                risk_per_trade_pct,
                tickers,
                earnings_blackout,
            )
            for setup in payload["setups"]:
                rows.append(_setup_to_row(job_id, strategy_name, setup))
        _persist_results(job_id, rows)
        _set_job_status(job_id, STATUS_COMPLETED, completed_at=_utcnow_iso())
    except HTTPException as exc:
        log.warning("scan job %s failed: %s", job_id, exc.detail)
        _set_job_status(
            job_id, STATUS_FAILED, completed_at=_utcnow_iso(), error=str(exc.detail)
        )
    except Exception as exc:  # pragma: no cover - defensive catch-all
        log.exception("scan job %s failed", job_id)
        _set_job_status(
            job_id, STATUS_FAILED, completed_at=_utcnow_iso(), error=str(exc)
        )


def _fetch_job(job_id: str) -> tuple[Any, ...] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT job_id, status, params, created_at, completed_at, error "
            "FROM scan_jobs WHERE job_id = ?",
            [job_id],
        ).fetchone()
        return tuple(row) if row is not None else None


def _fetch_results(job_id: str) -> list[tuple[Any, ...]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT ticker, strategy, direction, entry, stop, target, "
            "win_probability, r_multiple, trigger_reason, payload "
            "FROM scan_results WHERE job_id = ?",
            [job_id],
        ).fetchall()
        return [tuple(row) for row in rows]


def _fetch_recent_jobs(limit: int) -> list[tuple[Any, ...]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT job_id, status, created_at, completed_at, error "
            "FROM scan_jobs ORDER BY created_at DESC LIMIT ?",
            [limit],
        ).fetchall()
        return [tuple(row) for row in rows]


@router.post("/api/v1/scans", response_model=ScanJobCreated, status_code=202)
async def create_scan(
    request: ScanRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Kick off a background scan across `request.strategies` (default: all
    registered strategies) and return its job id immediately - the actual
    scan runs after the response is sent (see `_run_scan_job`)."""
    strategies = request.strategies or sorted(REGISTRY)
    unknown = [name for name in strategies if name not in REGISTRY]
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"Unknown strategy id(s): {unknown}"
        )

    job_id = uuid.uuid4().hex
    params = request.model_dump()
    params["strategies"] = strategies
    await run_in_threadpool(_create_job, job_id, params)

    background_tasks.add_task(
        _run_scan_job,
        job_id,
        strategies,
        settings,
        request.account_equity,
        request.risk_per_trade_pct,
        request.tickers,
        request.earnings_blackout,
    )
    return {"job_id": job_id}


@router.get("/api/v1/scans/{job_id}", response_model=ScanJobStatus)
async def get_scan(job_id: str) -> dict[str, Any]:
    """Job status, and - once `completed` - its persisted results."""
    row = await run_in_threadpool(_fetch_job, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown scan job {job_id!r}")

    job_id_, status, params, created_at, completed_at, error = row
    body: dict[str, Any] = {
        "job_id": job_id_,
        "status": status,
        "params": _as_json_obj(params),
        "created_at": _stringify_timestamp(created_at),
        "completed_at": _stringify_timestamp(completed_at),
        "error": error,
        "results": None,
    }
    if status == STATUS_COMPLETED:
        result_rows = await run_in_threadpool(_fetch_results, job_id)
        body["results"] = [
            {
                "ticker": r[0],
                "strategy": r[1],
                "direction": r[2],
                "entry": r[3],
                "stop": r[4],
                "target": r[5],
                "win_probability": r[6],
                "r_multiple": r[7],
                "trigger_reason": r[8],
                "payload": _as_json_obj(r[9]),
            }
            for r in result_rows
        ]
    return body


@router.get("/api/v1/scans", response_model=list[ScanJobSummary])
async def list_scans() -> list[dict[str, Any]]:
    """Most recent jobs first, capped at `MAX_JOBS_LISTED`."""
    rows = await run_in_threadpool(_fetch_recent_jobs, MAX_JOBS_LISTED)
    return [
        {
            "job_id": r[0],
            "status": r[1],
            "created_at": _stringify_timestamp(r[2]),
            "completed_at": _stringify_timestamp(r[3]),
            "error": r[4],
        }
        for r in rows
    ]
