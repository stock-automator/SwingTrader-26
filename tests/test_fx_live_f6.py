"""Batch 02 F6 live GBP/USD quote: latest-quote fetch, 60s cache, daily fallback."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.data.loader import DataUnavailableError
from backend.app.main import app

_base = itertools.count()


@pytest.fixture
def t0() -> datetime:
    """A distinct start time per test so the module cache cannot leak."""
    return datetime(2030, 1, 1, 12, 0, 0) + timedelta(days=next(_base))


def _daily() -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp("2026-11-19"), pd.Timestamp("2026-11-20")])
    c = pd.Series([1.25, 1.30], index=idx)
    return pd.DataFrame(
        {"Open": c, "High": c, "Low": c, "Close": c, "Volume": 0.0}, index=idx
    )


@pytest.fixture
def env(monkeypatch):
    """Mock yfinance (latest quote only) and the stored daily series."""
    s = {"price": 1.27, "raises": None, "fetches": 0, "daily": _daily(), "loads": []}

    class _FastInfo(dict):
        pass

    class _Ticker:
        def __init__(self, symbol):
            assert symbol == "GBPUSD=X"

        @property
        def fast_info(self):
            s["fetches"] += 1
            if s["raises"] is not None:
                raise s["raises"]
            return {"last_price": s["price"]}

        def history(self, *a, **k):
            raise AssertionError("live quote must not download history")

    def _load(ticker, *a, **k):
        s["loads"].append(k)
        if s["daily"] is None:
            raise DataUnavailableError(ticker)
        return s["daily"]

    monkeypatch.setattr("yfinance.Ticker", _Ticker)
    monkeypatch.setattr("yfinance.download", _no_download, raising=False)
    monkeypatch.setattr("backend.app.data.fx.load_prices", _load)
    return s


def _no_download(*a, **k):
    raise AssertionError("live quote must not call yf.download")


def test_f6_live_quote_returned(env, t0) -> None:
    from backend.app.data.fx import latest_gbp_rate

    r = latest_gbp_rate(now=t0)
    assert r.source == "live" and r.rate == pytest.approx(1.27)
    assert r.as_of == t0
    assert env["fetches"] == 1


def test_f6_cached_within_60s_then_refetch(env, t0) -> None:
    from backend.app.data.fx import latest_gbp_rate

    latest_gbp_rate(now=t0)
    env["price"] = 1.31
    assert latest_gbp_rate(now=t0 + timedelta(seconds=59)).rate == pytest.approx(1.27)
    assert env["fetches"] == 1
    r = latest_gbp_rate(now=t0 + timedelta(seconds=61))
    assert env["fetches"] == 2 and r.rate == pytest.approx(1.31)


@pytest.mark.parametrize(
    "bad", [RuntimeError("net"), None, float("nan"), 0.0, -1.2], ids=str
)
def test_f6_bad_live_falls_back_to_daily(env, t0, bad) -> None:
    from backend.app.data.fx import latest_gbp_rate

    if isinstance(bad, Exception):
        env["raises"] = bad
    else:
        env["price"] = bad
    r = latest_gbp_rate(now=t0)
    assert r.source == "daily" and r.rate == pytest.approx(1.30)
    assert r.as_of.date().isoformat() == "2026-11-20"
    assert all(k.get("allow_download") is False for k in env["loads"])


def test_f6_none_when_live_and_daily_missing(env, t0) -> None:
    from backend.app.data.fx import latest_gbp_rate

    env["raises"] = RuntimeError("down")
    env["daily"] = None
    assert latest_gbp_rate(now=t0) is None


def test_f6_usd_to_gbp_uses_live_rate(env, t0, monkeypatch) -> None:
    import backend.app.data.fx as fx

    real = fx.latest_gbp_rate
    monkeypatch.setattr(fx, "latest_gbp_rate", lambda now=None: real(now=t0))
    gbp, rate, _ = fx.usd_to_gbp(127.0)
    assert rate == pytest.approx(1.27) and gbp == pytest.approx(100.0)


def test_f6_usd_to_gbp_as_of_still_uses_daily(env) -> None:
    from datetime import date

    from backend.app.data.fx import usd_to_gbp

    _, rate, d = usd_to_gbp(1.0, as_of=date(2026, 11, 19))
    assert rate == pytest.approx(1.25) and d == date(2026, 11, 19)
    assert env["fetches"] == 0


# --- Order ticket -----------------------------------------------------------

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
    s = Settings(allow_downloads=False, data_dir=tmp_path)
    app.dependency_overrides[get_settings] = lambda: s
    yield TestClient(app)
    app.dependency_overrides.pop(get_settings, None)


def _ticket(client):
    r = client.post("/api/v1/order-ticket", json=TICKET)
    assert r.status_code == 200, r.text
    return r.json()["tickets"][0]


def _seam(monkeypatch, value):
    from backend.app.data.fx import FxRate  # noqa: F401

    monkeypatch.setattr("backend.app.data.fx.latest_gbp_rate", lambda now=None: value)


def test_f6_ticket_live_source_and_timestamp(client, env, monkeypatch) -> None:
    from backend.app.data.fx import FxRate

    _seam(monkeypatch, FxRate(1.25, datetime(2026, 11, 23, 14, 30, 5), "live"))
    t = _ticket(client)
    assert t["fx_rate_source"] == "live"
    assert t["fx_rate_date"].startswith("2026-11-23T14:30:05")
    assert t["notional_gbp"] == pytest.approx(t["notional_value"] / 1.25, abs=0.01)


def test_f6_ticket_daily_source(client, env, monkeypatch) -> None:
    from backend.app.data.fx import FxRate

    _seam(monkeypatch, FxRate(1.30, datetime(2026, 11, 20), "daily"))
    t = _ticket(client)
    assert t["fx_rate_source"] == "daily"
    assert t["fx_rate_date"].startswith("2026-11-20")


def test_f6_ticket_no_rate_source_null(client, env, monkeypatch) -> None:
    _seam(monkeypatch, None)
    t = _ticket(client)
    assert "fx_rate_source" in t and t["fx_rate_source"] is None
    assert t["fx_rate_date"] is None and t["risk_gbp"] is None
    assert t["tradable"] is True


# --- Offline branch and negative cache (rework loop 2) ----------------------


def test_f6_offline_context_never_constructs_ticker(env, t0, tmp_path, monkeypatch):
    import backend.app.data.fx as fx

    built: list[str] = []
    real = fx.yf.Ticker

    def _spy(symbol):
        built.append(symbol)
        return real(symbol)

    monkeypatch.setattr("yfinance.Ticker", _spy)
    with fx.fx_context(tmp_path, offline=True):
        r = fx.latest_gbp_rate(now=t0)
    assert r.source == "daily" and r.rate == pytest.approx(1.30)
    assert built == [] and env["fetches"] == 0
    with fx.fx_context(tmp_path, offline=False):
        r = fx.latest_gbp_rate(now=t0)
    assert r.source == "live" and built == ["GBPUSD=X"]


def test_f6_failed_live_fetch_not_retried_within_60s(env, t0) -> None:
    from backend.app.data.fx import latest_gbp_rate

    env["raises"] = RuntimeError("net")
    assert latest_gbp_rate(now=t0).source == "daily"
    assert env["fetches"] == 1
    r = latest_gbp_rate(now=t0 + timedelta(seconds=30))
    assert env["fetches"] == 1, "failed fetch must be negatively cached for 60s"
    assert r.source == "daily" and r.rate == pytest.approx(1.30)
    latest_gbp_rate(now=t0 + timedelta(seconds=61))
    assert env["fetches"] == 2
