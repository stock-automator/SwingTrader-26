"""
`POST /api/v1/data/sync` and `GET /api/v1/data/sync/status` — HTTP contract tests.

These exercise the route layer (status codes, response shapes, error handling)
rather than the ParquetSyncManager backend, which is covered by
`tests/test_parquet_sync.py`.
"""

from __future__ import annotations

import shutil
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
def client(tmp_path: Path) -> TestClient:
    # The sync job writes Parquet files into ``data_dir``. Point it at a
    # throwaway copy so running the suite never rewrites the tracked
    # ``data/raw/AAPL.parquet`` snapshot.
    data_dir = tmp_path / "raw"
    data_dir.mkdir()
    shutil.copy(Path("data/raw/AAPL.parquet"), data_dir / "AAPL.parquet")
    settings = Settings(
        data_dir=data_dir,
        allow_downloads=False,
        universe_path=Path("config/universe.json"),
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


# ---------------------------------------------------------------------------
# Cycle 01 - C-2: explicit request tickers are filtered through DENYLIST, so
# `POST /data/sync {"tickers": ["EQR"]}` can no longer re-download a
# delisted/renamed symbol.
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_sync(monkeypatch) -> list:
    """Replace the background task body so no fetch/disk write happens, and
    record exactly which tickers were handed to it."""
    import backend.app.api.data_sync as ds

    calls: list = []
    monkeypatch.setattr(
        ds, "_run_sync", lambda tickers, data_dir: calls.append(list(tickers))
    )
    return calls


class TestC2SyncSkipsDenied:
    def test_c2_denied_tickers_are_skipped_and_reported(
        self, client: TestClient, captured_sync: list
    ) -> None:
        resp = client.post(START_SYNC_URL, json={"tickers": ["AAPL", "EQR", "KVUE"]})
        assert resp.status_code == 202
        body = resp.json()
        assert body["tickers"] == ["AAPL"]
        assert body["skipped"] == ["EQR", "KVUE"]
        assert captured_sync == [["AAPL"]]

    def test_c2_lowercase_denied_ticker_is_still_skipped(
        self, client: TestClient, captured_sync: list
    ) -> None:
        resp = client.post(START_SYNC_URL, json={"tickers": ["aapl", " wbd "]})
        assert resp.status_code == 202
        body = resp.json()
        assert body["tickers"] == ["AAPL"]
        assert body["skipped"] == ["WBD"]

    @pytest.mark.parametrize(
        "ticker", ["EA", "KVUE", "WBD", "AVB", "EQR", "BK", "MMC", "CTRA", "K", "WBA"]
    )
    def test_c2_every_denied_ticker_is_skipped(
        self, client: TestClient, captured_sync: list, ticker: str
    ) -> None:
        resp = client.post(START_SYNC_URL, json={"tickers": ["AAPL", ticker]})
        assert resp.status_code == 202
        assert resp.json()["skipped"] == [ticker]
        assert captured_sync == [["AAPL"]]

    def test_c2_explicit_tickers_are_sanitized_and_deduped_before_deny(
        self, client: TestClient, captured_sync: list
    ) -> None:
        """Explicit tickers go through `universe.sanitize_ticker` (so
        `brk.b` -> `BRK-B`) and are deduped keeping first-seen order
        *before* the deny check, so a denied ticker is reported once."""
        resp = client.post(
            START_SYNC_URL,
            json={"tickers": ["EQR", "eqr", "brk.b", "AAPL", "aapl"]},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["tickers"] == ["BRK-B", "AAPL"]
        assert body["skipped"] == ["EQR"]
        assert captured_sync == [["BRK-B", "AAPL"]]

    def test_c2_all_denied_returns_422_and_queues_nothing(
        self, client: TestClient, captured_sync: list
    ) -> None:
        resp = client.post(START_SYNC_URL, json={"tickers": ["EQR", "AVB", "EA"]})
        assert resp.status_code == 422
        assert captured_sync == []
        assert _job_state.in_progress is False

    def test_c2_skipped_defaults_to_empty_list(
        self, client: TestClient, captured_sync: list
    ) -> None:
        resp = client.post(START_SYNC_URL, json={"tickers": ["AAPL", "VMRK"]})
        assert resp.status_code == 202
        body = resp.json()
        assert body["tickers"] == ["AAPL", "VMRK"]
        assert body["skipped"] == []

    def test_c2_watchlist_sync_never_queues_denied(
        self, client: TestClient, captured_sync: list
    ) -> None:
        resp = client.post(START_SYNC_URL, json={})
        assert resp.status_code == 202
        body = resp.json()
        denied = {"EA", "KVUE", "WBD", "AVB", "EQR", "BK", "MMC", "CTRA", "K", "WBA"}
        assert denied.isdisjoint(body["tickers"])
        assert body["skipped"] == []

    def test_c2_response_schema_declares_skipped(self) -> None:
        from backend.app.api.schemas import DataSyncResponse

        assert DataSyncResponse(status="started", tickers=["AAPL"]).skipped == []
