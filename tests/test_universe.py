"""
Tests for `backend.app.data.universe`: ticker sanitization, dedupe,
denylist/broken-data purging, `needs_sync()` staleness, and the
sync()-falls-back-to-a-static-seed-list path when the live fetch fails.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
import requests

from backend.app.data.universe import (
    DENYLIST,
    UniverseManager,
    UniverseSyncResult,
    sanitize_ticker,
)


def _write_parquet(path: Path, rows: int = 5) -> None:
    index = pd.date_range("2024-01-01", periods=rows, freq="B")
    df = pd.DataFrame(
        {
            "Open": [1.0] * rows,
            "High": [1.0] * rows,
            "Low": [1.0] * rows,
            "Close": [1.0] * rows,
            "Volume": [1_000] * rows,
        },
        index=index,
    )
    df.to_parquet(path)


@pytest.fixture
def manager(tmp_path) -> UniverseManager:
    return UniverseManager(
        config_path=tmp_path / "universe.json",
        data_dir=tmp_path / "raw",
    )


class TestSanitizeTicker:
    def test_uppercases_and_strips(self):
        assert sanitize_ticker("  aapl  ") == "AAPL"

    def test_dot_class_notation_becomes_hyphen(self):
        assert sanitize_ticker("BRK.B") == "BRK-B"
        assert sanitize_ticker("BF.B") == "BF-B"

    def test_already_hyphenated_is_unchanged(self):
        assert sanitize_ticker("BRK-B") == "BRK-B"

    def test_mixed_case_and_whitespace(self):
        assert sanitize_ticker(" brk.b\n") == "BRK-B"


class TestDedupeLogic:
    def test_merges_and_dedupes_case_and_notation_variants(self, manager):
        merged = manager._sanitize_and_dedupe(
            ["aapl", "AAPL", " Aapl ", "BRK.B", "BRK-B", "MSFT"]
        )
        assert merged == ["AAPL", "BRK-B", "MSFT"]

    def test_drops_blank_entries(self, manager):
        merged = manager._sanitize_and_dedupe(["AAPL", "  ", "", "MSFT"])
        assert merged == ["AAPL", "MSFT"]

    def test_preserves_first_seen_order(self, manager):
        merged = manager._sanitize_and_dedupe(["MSFT", "aapl", "msft", "AAPL"])
        assert merged == ["MSFT", "AAPL"]


class TestDenylistPurge:
    def test_denylisted_symbols_are_excluded_from_sync_result(self, manager):
        manager.fetch_sp500_constituents = lambda: ["AAPL", "BK", "K"]
        manager.fetch_nasdaq100_constituents = lambda: ["MSFT", "MMC"]
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        for ticker in ("AAPL", "MSFT"):
            _write_parquet(manager.data_dir / f"{ticker}.parquet")

        result = manager.sync()

        assert set(result.symbols) == {"AAPL", "MSFT"}
        assert DENYLIST.isdisjoint(result.symbols)

    def test_denylist_does_not_touch_existing_parquet_files(self, manager):
        # BK has good data on disk; denylisting must not delete it, only
        # keep it out of the active universe list.
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        bk_path = manager.data_dir / "BK.parquet"
        _write_parquet(bk_path)

        manager.fetch_sp500_constituents = lambda: ["AAPL", "BK"]
        manager.fetch_nasdaq100_constituents = lambda: []
        _write_parquet(manager.data_dir / "AAPL.parquet")

        result = manager.sync()

        assert "BK" not in result.symbols
        assert bk_path.exists()
        assert len(pd.read_parquet(bk_path)) == 5


class TestPurgeBrokenSymbols:
    def test_missing_file_is_excluded(self, manager):
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        _write_parquet(manager.data_dir / "AAPL.parquet")

        active = manager.purge_broken_symbols(["AAPL", "NOPE"])

        assert active == ["AAPL"]

    def test_zero_row_file_is_excluded(self, manager):
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        empty_path = manager.data_dir / "EMPTY.parquet"
        pd.DataFrame({"Close": []}, index=pd.DatetimeIndex([])).to_parquet(empty_path)
        _write_parquet(manager.data_dir / "AAPL.parquet")

        active = manager.purge_broken_symbols(["AAPL", "EMPTY"])

        assert active == ["AAPL"]
        # Non-destructive: the empty file is still on disk, untouched.
        assert empty_path.exists()

    def test_good_data_is_kept(self, manager):
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        _write_parquet(manager.data_dir / "AAPL.parquet")
        _write_parquet(manager.data_dir / "MSFT.parquet")

        active = manager.purge_broken_symbols(["AAPL", "MSFT"])

        assert sorted(active) == ["AAPL", "MSFT"]


class TestNeedsSync:
    def test_missing_file_needs_sync(self, manager):
        assert manager.needs_sync() is True

    def test_fresh_file_does_not_need_sync(self, manager):
        manager.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbols": ["AAPL"],
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "source_counts": {"sp500": 1, "nasdaq100": 0},
        }
        manager.config_path.write_text(json.dumps(payload))

        assert manager.needs_sync() is False

    def test_stale_file_needs_sync(self, manager):
        manager.config_path.parent.mkdir(parents=True, exist_ok=True)
        stale = datetime.now(timezone.utc) - timedelta(hours=25)
        payload = {
            "symbols": ["AAPL"],
            "synced_at": stale.isoformat(),
            "source_counts": {"sp500": 1, "nasdaq100": 0},
        }
        manager.config_path.write_text(json.dumps(payload))

        assert manager.needs_sync() is True

    def test_just_under_24h_does_not_need_sync(self, manager):
        manager.config_path.parent.mkdir(parents=True, exist_ok=True)
        almost_stale = datetime.now(timezone.utc) - timedelta(hours=23, minutes=59)
        payload = {
            "symbols": ["AAPL"],
            "synced_at": almost_stale.isoformat(),
            "source_counts": {"sp500": 1, "nasdaq100": 0},
        }
        manager.config_path.write_text(json.dumps(payload))

        assert manager.needs_sync() is False

    def test_malformed_json_needs_sync(self, manager):
        manager.config_path.parent.mkdir(parents=True, exist_ok=True)
        manager.config_path.write_text("{not valid json")

        assert manager.needs_sync() is True

    def test_missing_synced_at_needs_sync(self, manager):
        manager.config_path.parent.mkdir(parents=True, exist_ok=True)
        manager.config_path.write_text(json.dumps({"symbols": ["AAPL"]}))

        assert manager.needs_sync() is True


class TestFetchHeaders:
    def test_fetch_tables_sends_browser_user_agent(self, manager, monkeypatch):
        """Regression: Wikipedia (and similar constituent-list sources) can
        403 the bare `python-requests/x.y` UA some hosts get flagged under.
        `_fetch_tables` must fetch with a realistic browser User-Agent
        rather than handing the URL straight to `pd.read_html`."""
        captured: dict = {}

        class _FakeResponse:
            text = "<table><tr><th>Symbol</th></tr><tr><td>AAPL</td></tr></table>"

            def raise_for_status(self):
                pass

        def fake_get(url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["timeout"] = timeout
            return _FakeResponse()

        monkeypatch.setattr("backend.app.data.universe.requests.get", fake_get)

        tables = manager._fetch_tables("https://en.wikipedia.org/wiki/Test")

        assert captured["url"] == "https://en.wikipedia.org/wiki/Test"
        assert "User-Agent" in captured["headers"]
        assert "Mozilla" in captured["headers"]["User-Agent"]
        assert captured["timeout"] is not None
        assert tables[0]["Symbol"].tolist() == ["AAPL"]

    def test_403_response_falls_back_to_static_list(self, manager, monkeypatch):
        def fake_get(url, headers=None, timeout=None):
            response = requests.Response()
            response.status_code = 403
            raise requests.HTTPError("403 Client Error", response=response)

        monkeypatch.setattr("backend.app.data.universe.requests.get", fake_get)

        symbols = manager.fetch_sp500_constituents()

        assert "AAPL" in symbols
        assert len(symbols) > 50


class TestSyncFallback:
    def test_sp500_fetch_raises_falls_back_to_static_list(self, manager, monkeypatch):
        def _boom():
            raise RuntimeError("no network in this sandbox")

        monkeypatch.setattr(
            pd,
            "read_html",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("no network in this sandbox")
            ),
        )

        symbols = manager.fetch_sp500_constituents()

        assert "AAPL" in symbols
        assert len(symbols) > 50

    def test_nasdaq100_fetch_raises_falls_back_to_static_list(
        self, manager, monkeypatch
    ):
        monkeypatch.setattr(
            pd,
            "read_html",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("no network in this sandbox")
            ),
        )

        symbols = manager.fetch_nasdaq100_constituents()

        assert "NVDA" in symbols
        assert len(symbols) > 20

    def test_sync_still_writes_a_result_when_network_is_unavailable(
        self, manager, monkeypatch
    ):
        monkeypatch.setattr(
            pd,
            "read_html",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("no network in this sandbox")
            ),
        )
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        # Give a handful of the static-fallback names real data so the
        # active list isn't empty after the broken-data purge.
        for ticker in ("AAPL", "MSFT", "NVDA"):
            _write_parquet(manager.data_dir / f"{ticker}.parquet")

        result = manager.sync()

        assert manager.config_path.exists()
        assert isinstance(result, UniverseSyncResult)
        assert result.source_counts["sp500"] > 50
        assert result.source_counts["nasdaq100"] > 20
        assert set(result.symbols) == {"AAPL", "MSFT", "NVDA"}

        on_disk = json.loads(manager.config_path.read_text())
        assert on_disk["symbols"] == result.symbols
        assert on_disk["synced_at"] == result.synced_at
        assert on_disk["source_counts"] == result.source_counts


class TestReadRoundTrip:
    def test_read_after_sync_matches(self, manager, monkeypatch):
        monkeypatch.setattr(
            pd,
            "read_html",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        _write_parquet(manager.data_dir / "AAPL.parquet")

        synced = manager.sync()
        read_back = manager.read()

        assert read_back is not None
        assert read_back.as_dict() == synced.as_dict()

    def test_read_returns_none_when_never_synced(self, manager):
        assert manager.read() is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
