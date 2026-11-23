"""Batch 02 F6 rework 1: GBPUSD=X is synced into the cache and the order
ticket reads it cache-only."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.api import data_sync
from backend.app.config import Settings, get_settings
from backend.app.data import fx
from backend.app.data.universe import sanitize_ticker
from backend.app.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
FX_PARQUET = REPO_ROOT / "data" / "raw" / "GBPUSD=X.parquet"

TICKET = {
    "ticker": "AAPL",
    "entry_price": 100.0,
    "sl_type": "ATR",
    "sl_value": 2.0,
    "tp_type": "ATR",
    "tp_value": 5.0,
    "atr": 1.0,
    "account_tiers": [10_000.0],
}


@pytest.fixture
def client(tmp_path: Path):
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"symbols": ["AAPL", "MSFT"], "names": {}}))
    settings = Settings(
        allow_downloads=False, data_dir=tmp_path, universe_path=universe
    )
    app.dependency_overrides[get_settings] = lambda: settings
    data_sync._job_state.in_progress = False
    yield TestClient(app)
    app.dependency_overrides.pop(get_settings, None)
    data_sync._job_state.in_progress = False
    data_sync._job_state.results.clear()


def test_f6_sanitize_keeps_fx_symbol() -> None:
    assert sanitize_ticker("gbpusd=x") == "GBPUSD=X"


def test_f6_default_sync_includes_gbpusd(client, monkeypatch) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr(data_sync, "_run_sync", lambda t, d: ran.append(list(t)))
    r = client.post("/api/v1/data/sync", json={})
    assert r.status_code == 202
    assert "GBPUSD=X" in r.json()["tickers"]
    assert "GBPUSD=X" not in r.json()["skipped"]
    assert {"AAPL", "MSFT"} <= set(r.json()["tickers"])
    assert ran and "GBPUSD=X" in ran[0]


def test_f6_default_sync_gbpusd_not_duplicated(client, tmp_path, monkeypatch) -> None:
    (tmp_path / "universe.json").write_text(
        json.dumps({"symbols": ["AAPL", "GBPUSD=X"], "names": {}})
    )
    monkeypatch.setattr(data_sync, "_run_sync", lambda t, d: None)
    tickers = client.post("/api/v1/data/sync", json={}).json()["tickers"]
    assert tickers.count("GBPUSD=X") == 1


def _post(client, monkeypatch, result):
    """The ticket now reads `fx.latest_gbp_rate` (live quote, else daily);
    patch that seam with a fixed `FxRate` or None."""
    monkeypatch.setattr("backend.app.data.fx.latest_gbp_rate", lambda *a, **k: result)
    r = client.post("/api/v1/order-ticket", json=TICKET)
    assert r.status_code == 200, r.text
    return r.json()["tickets"][0]


def test_f6_ticket_never_downloads_history(client, monkeypatch) -> None:
    """Even with downloads allowed, the daily fallback reads the cache only
    (no full-history download per request)."""
    app.dependency_overrides[get_settings] = lambda: Settings(
        allow_downloads=True, data_dir=Path("/nonexistent-fx")
    )
    calls: list[bool] = []

    def fake_load_prices(ticker, data_dir=None, allow_download=True, **_):
        calls.append(allow_download)
        raise fx.DataUnavailableError("not cached")

    monkeypatch.setattr(fx, "_live_quote", lambda now: None)
    monkeypatch.setattr(fx, "load_prices", fake_load_prices)
    r = client.post("/api/v1/order-ticket", json=TICKET)
    assert r.status_code == 200, r.text
    assert calls == [False]


def test_f6_ticket_has_fx_rate_date(client, monkeypatch) -> None:
    t = _post(client, monkeypatch, fx.FxRate(1.25, datetime(2026, 11, 20), "daily"))
    assert t["fx_rate_date"].startswith("2026-11-20")
    assert t["fx_note"] is None
    assert t["notional_gbp"] is not None


def test_f6_ticket_no_rate_note_and_null_date(client, monkeypatch) -> None:
    t = _post(client, monkeypatch, None)
    assert "fx_rate_date" in t and t["fx_rate_date"] is None
    assert t["risk_gbp"] is None and t["notional_gbp"] is None
    assert "GBP rate unavailable" in t["fx_note"]
    assert "no cached" not in t["fx_note"].lower()


def test_f6_repo_gbpusd_cached_and_fresh() -> None:
    assert FX_PARQUET.exists(), "data/raw/GBPUSD=X.parquet is not committed"
    df = pd.read_parquet(FX_PARQUET)
    last = pd.Timestamp(df.index.max())
    if last.tzinfo is not None:
        last = last.tz_localize(None)
    assert last.date() >= date(2026, 11, 19)
    closes = df["Close"].dropna()
    assert len(closes) > 100 and (closes > 0).all()


def test_f2_default_sync_includes_spy_once(client, monkeypatch) -> None:
    monkeypatch.setattr(data_sync, "_run_sync", lambda t, d: None)
    r = client.post("/api/v1/data/sync", json={})
    assert r.status_code == 202
    tickers = r.json()["tickers"]
    assert tickers.count("SPY") == 1
    assert tickers.count("GBPUSD=X") == 1


def test_f2_spy_parquet_is_committed_and_fresh() -> None:
    path = REPO_ROOT / "data" / "raw" / "SPY.parquet"
    assert path.exists()
    df = pd.read_parquet(path)
    assert (df["Close"] > 0).all()
    assert pd.Timestamp(df.index.max()) >= pd.Timestamp("2026-11-19")
