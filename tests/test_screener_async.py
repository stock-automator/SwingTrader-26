"""
Tests for `POST/GET /api/v1/scans` - the async background scanner
(`backend/app/api/scans.py`) and its DuckDB persistence
(`backend/app/db/session.py`).

`scans_router` isn't wired into `backend.app.main.app` yet (a separate pass
wires it in centrally), so these tests mount it on a small standalone
FastAPI app instead of importing `backend.app.main`. That exercises the
exact same router/dependency code that will run once it's included there.

Price loading is monkeypatched to synthetic OHLCV data, mirroring
`tests/test_api.py`'s `fake_prices` fixture, so nothing here touches the
network or the parquet cache. Each test points `SCANS_DB_PATH` at its own
`tmp_path` file and resets the shared DuckDB connection before/after, so
tests never share state with each other or with a real `data/scans.duckdb`.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.app.api.screener as screener_module
import backend.app.db.session as session
from backend.app.api.scans import router as scans_router
from backend.app.data.loader import DataUnavailableError


def _trending_ohlcv(n: int = 300, seed: int = 7, drift: float = 0.3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2022-01-03", periods=n, freq="B")
    noise = rng.normal(0, 1.0, n).cumsum()
    close = pd.Series(100.0 + np.arange(n) * drift + noise, index=index).clip(lower=5.0)

    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": pd.Series(1_000_000.0, index=index),
        },
        index=index,
    )


@pytest.fixture(autouse=True)
def _isolated_duckdb(tmp_path, monkeypatch):
    """Point the shared DuckDB connection at a throwaway file per test."""
    db_path = tmp_path / "scans.duckdb"
    monkeypatch.setenv("SCANS_DB_PATH", str(db_path))
    session.reset_for_tests()
    yield
    session.reset_for_tests()


@pytest.fixture
def fake_prices(monkeypatch):
    """Same synthetic universe/patch pattern as `tests/test_api.py`."""
    universe = {
        "AAPL": _trending_ohlcv(seed=1),
        "MSFT": _trending_ohlcv(seed=2, drift=0.2),
        "SPY": _trending_ohlcv(seed=3, drift=0.15),
    }

    def _load_prices(ticker, start=None, end=None, data_dir=None, allow_download=True):
        ticker = ticker.upper()
        if ticker not in universe:
            raise DataUnavailableError(f"{ticker} not in test universe")
        df = universe[ticker]
        if start is not None:
            df = df[df.index >= pd.Timestamp(start)]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end)]
        return df

    monkeypatch.setattr(screener_module, "load_prices", _load_prices)
    monkeypatch.setattr(
        screener_module, "load_watchlist", lambda settings: ["AAPL", "MSFT"]
    )
    return universe


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(scans_router)
    return TestClient(app)


def _poll_until_done(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/scans/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"scan job {job_id} did not finish within {timeout}s")


class TestCreateAndPollScan:
    def test_scan_completes_and_persists_results(self, client, fake_prices):
        response = client.post(
            "/api/v1/scans",
            json={
                "strategies": ["donchian_breakout"],
                "tickers": ["AAPL", "MSFT"],
            },
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert job_id

        body = _poll_until_done(client, job_id)
        assert body["status"] == "completed"
        assert body["error"] is None
        assert body["params"]["strategies"] == ["donchian_breakout"]
        assert isinstance(body["results"], list)

        for row in body["results"]:
            assert row["ticker"] in {"AAPL", "MSFT"}
            assert row["strategy"] == "donchian_breakout"
            assert row["direction"] in {"LONG", "SHORT", "EXIT_LONG"}
            assert row["trigger_reason"]
            assert isinstance(row["payload"], dict)
            assert row["payload"]["ticker"] == row["ticker"]

    def test_results_land_in_duckdb_directly(self, client, fake_prices):
        response = client.post(
            "/api/v1/scans",
            json={
                "strategies": ["donchian_breakout"],
                "tickers": ["AAPL", "MSFT"],
            },
        )
        job_id = response.json()["job_id"]
        body = _poll_until_done(client, job_id)
        assert body["status"] == "completed"

        with session.get_connection() as conn:
            job_row = conn.execute(
                "SELECT status FROM scan_jobs WHERE job_id = ?", [job_id]
            ).fetchone()
            assert job_row is not None
            assert job_row[0] == "completed"

            result_count = conn.execute(
                "SELECT COUNT(*) FROM scan_results WHERE job_id = ?", [job_id]
            ).fetchone()[0]
            assert result_count == len(body["results"])

    def test_defaults_to_every_registered_strategy(self, client, fake_prices):
        response = client.post("/api/v1/scans", json={"tickers": ["AAPL", "MSFT"]})
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        body = _poll_until_done(client, job_id)
        assert body["status"] == "completed"
        assert len(body["params"]["strategies"]) == 10

    def test_unknown_strategy_is_422(self, client, fake_prices):
        response = client.post(
            "/api/v1/scans", json={"strategies": ["not_a_real_strategy"]}
        )
        assert response.status_code == 422

    def test_unknown_job_id_is_404(self, client):
        response = client.get("/api/v1/scans/does-not-exist")
        assert response.status_code == 404


class TestListScans:
    def test_created_job_appears_most_recent_first(self, client, fake_prices):
        first = client.post(
            "/api/v1/scans",
            json={"strategies": ["donchian_breakout"], "tickers": ["AAPL"]},
        ).json()["job_id"]
        _poll_until_done(client, first)

        second = client.post(
            "/api/v1/scans",
            json={"strategies": ["moving_average_cross"], "tickers": ["AAPL"]},
        ).json()["job_id"]
        _poll_until_done(client, second)

        response = client.get("/api/v1/scans")
        assert response.status_code == 200
        job_ids = [job["job_id"] for job in response.json()]
        assert first in job_ids
        assert second in job_ids
        assert job_ids.index(second) < job_ids.index(first)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
