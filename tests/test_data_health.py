"""
`GET /api/v1/data/health` - HTTP contract tests for cycle 01 (delisted
tickers).

- C-2: the response carries a `delisted` list built from
  `data.universe.DENY_ENTRIES` (every entry whose kind is not
  `broken_data`), in both the empty-cache and populated-cache paths, and is
  declared through a `DataHealthResponse` response model.
- C-3: a ticker that is merely *stale* (e.g. a trading halt) is flagged in
  `stale_tickers` and its parquet file is never deleted or moved.

Every test points `data_dir` at `tmp_path`; nothing here touches `data/raw/`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backend.app.api.data_health as data_health_mod
import backend.app.data.universe as universe_mod
from backend.app.config import Settings, get_settings
from backend.app.main import app

HEALTH_URL = "/api/v1/data/health"

#: The stale-report fields that existed before cycle 01 and must not change.
LEGACY_FIELDS = {
    "total_tickers",
    "newest_ticker",
    "newest_date",
    "oldest_ticker",
    "oldest_date",
    "stale_count",
    "stale_tickers",
    "total_rows_estimate",
    "as_of",
}

#: ticker -> (since ISO, kind, successor), as agreed at Gate 1.
EXPECTED_DELISTED = {
    "EA": ("2026-08-04", "cash_buyout", None),
    "KVUE": ("2026-11-02", "stock_merger", "KMB"),
    "WBD": ("2026-10-06", "cash_buyout", None),
    "AVB": ("2026-08-18", "stock_merger", "VMRK"),
    "EQR": ("2026-08-18", "rename", "VMRK"),
    "BK": ("2026-05-21", "rename", "BNY"),
    "MMC": ("2026-01-14", "rename", "MRSH"),
    "CTRA": ("2026-05-07", "stock_merger", "DVN"),
    "K": ("2025-12-11", "cash_buyout", None),
    "WBA": ("2025-08-28", "cash_buyout", None),
}


def _write_bars(path: Path, last_day: date, rows: int = 10) -> None:
    index = pd.bdate_range(end=pd.Timestamp(last_day), periods=rows)
    pd.DataFrame(
        {
            "Open": 1.0,
            "High": 1.0,
            "Low": 1.0,
            "Close": 1.0,
            "Volume": 1_000,
        },
        index=index,
    ).to_parquet(path)


def _client(data_dir: Path, stale_days: int | None = None) -> TestClient:
    settings = Settings(
        data_dir=data_dir,
        allow_downloads=False,
        screener_stale_after_days=stale_days,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "raw"
    data_dir.mkdir()
    return data_dir


@pytest.fixture
def populated_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "raw"
    data_dir.mkdir()
    today = datetime.now(timezone.utc).date()
    _write_bars(data_dir / "AAPL.parquet", today)
    _write_bars(data_dir / "MSFT.parquet", today - timedelta(days=1))
    return data_dir


def _assert_delisted_matches_contract(delisted: list) -> None:
    assert isinstance(delisted, list)
    by_ticker = {row["ticker"]: row for row in delisted}
    assert len(by_ticker) == len(delisted), "duplicate tickers in delisted"
    assert set(by_ticker) == set(EXPECTED_DELISTED)
    for ticker, (since, kind, successor) in EXPECTED_DELISTED.items():
        row = by_ticker[ticker]
        assert set(row) == {"ticker", "since", "kind", "reason", "successor"}
        assert row["since"] == since
        assert row["kind"] == kind
        assert row["successor"] == successor
        assert isinstance(row["reason"], str) and row["reason"].strip()


# ---------------------------------------------------------------------------
# C-2: delisted list
# ---------------------------------------------------------------------------


class TestC2DelistedList:
    def test_c2_empty_cache_reports_delisted(self, empty_dir: Path) -> None:
        resp = _client(empty_dir).get(HEALTH_URL)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_tickers"] == 0
        _assert_delisted_matches_contract(body["delisted"])

    def test_c2_populated_cache_reports_delisted(self, populated_dir: Path) -> None:
        resp = _client(populated_dir).get(HEALTH_URL)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_tickers"] == 2
        _assert_delisted_matches_contract(body["delisted"])

    def test_c2_missing_data_dir_reports_delisted(self, tmp_path: Path) -> None:
        resp = _client(tmp_path / "does-not-exist").get(HEALTH_URL)
        assert resp.status_code == 200
        _assert_delisted_matches_contract(resp.json()["delisted"])

    @pytest.mark.parametrize("fixture_name", ["empty_dir", "populated_dir"])
    def test_c2_legacy_stale_fields_unchanged(
        self, request: pytest.FixtureRequest, fixture_name: str
    ) -> None:
        data_dir = request.getfixturevalue(fixture_name)
        body = _client(data_dir).get(HEALTH_URL).json()
        assert LEGACY_FIELDS <= set(body)
        assert body["stale_count"] == 0
        assert body["stale_tickers"] == []

    def test_c2_broken_data_entries_are_not_listed_as_delisted(
        self, empty_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entries = dict(universe_mod.DENY_ENTRIES)
        entries["ZZZZ"] = universe_mod.DenyEntry(
            kind="broken_data", since=None, reason="zero-bar feed"
        )
        # Patch wherever the implementation reads it from.
        monkeypatch.setattr(universe_mod, "DENY_ENTRIES", entries)
        monkeypatch.setattr(data_health_mod, "DENY_ENTRIES", entries, raising=False)

        body = _client(empty_dir).get(HEALTH_URL).json()

        tickers = [row["ticker"] for row in body["delisted"]]
        assert "ZZZZ" not in tickers
        assert set(tickers) == set(EXPECTED_DELISTED)

    def test_c2_delisted_ticker_with_leftover_file_is_flagged_stale_and_kept(
        self, empty_dir: Path
    ) -> None:
        """A leftover KVUE file is reported honestly: counted, flagged stale,
        listed as delisted - and the health check never removes it."""
        path = empty_dir / "KVUE.parquet"
        _write_bars(path, date(2026, 10, 30))
        body = _client(empty_dir, stale_days=5).get(HEALTH_URL).json()
        assert path.exists()
        assert body["total_tickers"] == 1
        assert "KVUE" in body["stale_tickers"]
        assert "KVUE" in {row["ticker"] for row in body["delisted"]}


class TestC2ResponseModel:
    def test_c2_openapi_declares_data_health_response(self) -> None:
        spec = TestClient(app).get("/openapi.json").json()
        schema = spec["paths"][HEALTH_URL]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert schema.get("$ref", "").endswith("/DataHealthResponse")

        components = spec["components"]["schemas"]
        props = components["DataHealthResponse"]["properties"]
        assert "delisted" in props
        assert LEGACY_FIELDS <= set(props)
        entry_props = components["DelistedEntry"]["properties"]
        assert set(entry_props) >= {"ticker", "since", "kind", "reason", "successor"}

    def test_c2_openapi_delisted_kind_is_enum(self) -> None:
        spec = TestClient(app).get("/openapi.json").json()
        components = spec["components"]["schemas"]
        kind = components["DelistedEntry"]["properties"]["kind"]
        if "$ref" in kind:
            kind = components[kind["$ref"].rsplit("/", 1)[-1]]
        assert "enum" in kind, f"DelistedEntry.kind is not an enum: {kind}"
        assert set(kind["enum"]) == {
            "rename",
            "stock_merger",
            "cash_buyout",
            "broken_data",
        }

    def test_c2_schemas_module_exports_models(self) -> None:
        from backend.app.api.schemas import DataHealthResponse, DelistedEntry

        entry = DelistedEntry(
            ticker="EA",
            since="2026-08-04",
            kind="cash_buyout",
            reason="taken private",
            successor=None,
        )
        assert entry.successor is None
        assert "delisted" in DataHealthResponse.model_fields


# ---------------------------------------------------------------------------
# C-3: stale is flagged, never deleted
# ---------------------------------------------------------------------------


class TestC3StaleIsFlaggedNotDeleted:
    def test_c3_thirty_day_old_ticker_is_stale_and_file_kept(
        self, empty_dir: Path
    ) -> None:
        today = datetime.now(timezone.utc).date()
        halted = empty_dir / "HALT.parquet"
        fresh = empty_dir / "AAPL.parquet"
        _write_bars(halted, today - timedelta(days=30))
        _write_bars(fresh, today)
        before = halted.read_bytes()

        resp = _client(empty_dir, stale_days=5).get(HEALTH_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["stale_tickers"] == ["HALT"]
        assert body["stale_count"] == 1
        assert body["total_tickers"] == 2
        assert halted.exists()
        assert halted.read_bytes() == before
        assert sorted(p.name for p in empty_dir.iterdir()) == [
            "AAPL.parquet",
            "HALT.parquet",
        ]

    def test_c3_stale_ticker_is_not_reported_as_delisted(self, empty_dir: Path) -> None:
        today = datetime.now(timezone.utc).date()
        _write_bars(empty_dir / "HALT.parquet", today - timedelta(days=30))
        body = _client(empty_dir, stale_days=5).get(HEALTH_URL).json()
        assert "HALT" not in {row["ticker"] for row in body.get("delisted", [])}

    def test_c3_repeated_health_calls_never_change_the_cache(
        self, empty_dir: Path
    ) -> None:
        today = datetime.now(timezone.utc).date()
        _write_bars(empty_dir / "HALT.parquet", today - timedelta(days=30))
        client = _client(empty_dir, stale_days=5)
        snapshot = {p.name: p.read_bytes() for p in empty_dir.iterdir()}
        for _ in range(3):
            assert client.get(HEALTH_URL).status_code == 200
        assert {p.name: p.read_bytes() for p in empty_dir.iterdir()} == snapshot
        assert not (empty_dir.parent / "delisted").exists()
