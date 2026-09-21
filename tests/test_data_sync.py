"""
`POST /api/v1/data/sync` and `GET /api/v1/data/sync/status` — HTTP contract tests.

These exercise the route layer (status codes, response shapes, error handling)
rather than the ParquetSyncManager backend, which is covered by
`tests/test_parquet_sync.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api.data_sync import _job_state
from backend.app.config import Settings, get_settings
from backend.app.main import app

SYNC_REQUEST = {"tickers": ["AAPL"]}
START_SYNC_URL = "/api/v1/data/sync"
SYNC_STATUS_URL = "/api/v1/data/sync/status"


@pytest.fixture(autouse=True)
def reset_sync_state():
    _job_state.in_progress = False
    _job_state.started_at = None
    _job_state.results.clear()
    yield


@pytest.fixture
def client() -> TestClient:
    settings = Settings(
        data_dir=Path("data/raw"),
        allow_downloads=False,
        watchlist_path=Path("config/watchlist.txt"),
        screener_max_tickers=750,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


class TestStartDataSync:
    def test_returns_202_with_tickers(self, client: TestClient) -> None:
        resp = client.post(START_SYNC_URL, json=SYNC_REQUEST)
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "started"
        assert body["tickers"] == ["AAPL"]

    def test_omitting_tickers_uses_watchlist(self, client: TestClient) -> None:
        import backend.app.api.data_sync as ds

        original = ds._run_sync

        def _no_op(*args, **kwargs):
            pass

        ds._run_sync = _no_op
        try:
            resp = client.post(START_SYNC_URL, json={})
        finally:
            ds._run_sync = original

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "started"
        assert len(body["tickers"]) > 0


class TestDataSyncStatus:
    def test_every_result_has_last_fetched_at(self, client: TestClient) -> None:
        """Every SyncResultResponse row includes a last_fetched_at field,
        regardless of what global state other tests have left."""
        resp = client.get(SYNC_STATUS_URL)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["results"], list)
        for r in body["results"]:
            assert (
                "last_fetched_at" in r
            ), f"SyncResultResponse missing last_fetched_at: {r}"
            laf = r["last_fetched_at"]
            assert laf is None or (
                isinstance(laf, str) and len(laf) == 10
            ), f"last_fetched_at has wrong shape: {laf!r}"
