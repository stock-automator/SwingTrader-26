"""
DuckDB connection management for background scan jobs
(`backend/app/api/scans.py`).

Concurrency model
------------------
DuckDB is an embedded, single-process database engine: it does not support
many concurrent read-write connections against the same file safely (a
second writer connection can raise a lock conflict while the first is
mid-transaction). Rather than opening a fresh connection per caller, this
module keeps exactly **one** shared `duckdb.DuckDBPyConnection` per process,
guarded by a module-level `threading.Lock`. Every read *and* write - job
creation, status transitions, result inserts, and the status-poll/list GET
endpoints' queries - takes the lock for the duration of its statement via
`get_connection()`.

This is safe and simple because every statement here is a single, fast
INSERT/UPDATE/SELECT: the lock is only ever held for the few milliseconds a
DuckDB query takes, never across the actual CPU-bound strategy scan (that
work happens outside the lock, in a background thread - see
`api/scans.py::_run_scan_job`). So a background scan writing its results
never blocks an HTTP GET polling job status for more than a query's worth
of time, and the reverse holds too.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

#: backend/app/db/session.py -> db -> app -> backend -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: Default DuckDB file. Overridable via `SCANS_DB_PATH` (used by tests to
#: point at a throwaway file instead of the real `data/scans.duckdb`).
DEFAULT_DB_PATH = _REPO_ROOT / "data" / "scans.duckdb"

_lock = threading.Lock()
_connection: duckdb.DuckDBPyConnection | None = None
_connection_path: Path | None = None


def _resolve_db_path() -> Path:
    override = os.environ.get("SCANS_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def _create_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_jobs (
            job_id TEXT PRIMARY KEY,
            status TEXT,
            params JSON,
            created_at TIMESTAMP,
            completed_at TIMESTAMP,
            error TEXT
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_results (
            job_id TEXT,
            ticker TEXT,
            strategy TEXT,
            direction TEXT,
            entry DOUBLE,
            stop DOUBLE,
            target DOUBLE,
            win_probability DOUBLE,
            r_multiple DOUBLE,
            trigger_reason TEXT,
            payload JSON
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS routed_orders (
            order_id TEXT PRIMARY KEY,
            broker TEXT,
            ticker TEXT,
            side TEXT,
            order_type TEXT,
            qty DOUBLE,
            limit_price DOUBLE,
            status TEXT,
            fill_price DOUBLE,
            broker_order_id TEXT,
            error TEXT,
            submitted_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """)
    # `job_id`/`order_id` PKs above already give scan_jobs/routed_orders a
    # lookup index for free. The three indexes below cover the remaining
    # hot filter columns that don't have one: scan_results has no PK at all
    # (api/scans.py::_fetch_results filters it by job_id on every job-status
    # poll), and routed_orders is filtered by status (_fetch_active_orders)
    # and range-scanned by updated_at (list_orders_updated_since, polled
    # every settings.ws_poll_seconds by the live-feed WebSocket).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_results_job_id " "ON scan_results(job_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_routed_orders_status "
        "ON routed_orders(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_routed_orders_updated_at "
        "ON routed_orders(updated_at)"
    )


def _get_shared_connection_locked() -> duckdb.DuckDBPyConnection:
    """Return the process-wide connection, (re)opening it if this is the
    first call or `SCANS_DB_PATH` changed since the last one (test
    isolation between modules). Caller must already hold `_lock`."""
    global _connection, _connection_path
    path = _resolve_db_path()
    if _connection is None or _connection_path != path:
        if _connection is not None:
            _connection.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        _connection = duckdb.connect(str(path))
        _connection_path = path
        _create_tables(_connection)
    return _connection


def init_db() -> None:
    """Create the DuckDB file (if missing) and its tables (if missing).

    Safe to call repeatedly and from multiple threads - `get_connection`
    performs the same lazy setup, so this exists mainly to make "make sure
    the database is ready" an explicit, named step callers can take up
    front (e.g. at application startup).
    """
    with _lock:
        _get_shared_connection_locked()


@contextmanager
def get_connection() -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield the shared DuckDB connection with the module lock held.

    Every statement - reads and writes alike - should go through this
    context manager rather than calling `duckdb.connect` directly; see the
    module docstring for why a single lock-guarded connection is the chosen
    concurrency model here.
    """
    with _lock:
        yield _get_shared_connection_locked()


def reset_for_tests() -> None:
    """Close the shared connection so the next `get_connection()`/`init_db()`
    call reopens against whatever `SCANS_DB_PATH` currently points at.

    Test-only: lets each test module point `SCANS_DB_PATH` at its own
    throwaway file without leaking a stale connection (and its lock) from a
    previous test's path.
    """
    global _connection, _connection_path
    with _lock:
        if _connection is not None:
            _connection.close()
        _connection = None
        _connection_path = None


__all__ = ["get_connection", "init_db", "reset_for_tests", "DEFAULT_DB_PATH"]
