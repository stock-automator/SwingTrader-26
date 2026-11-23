"""
Tests for `backend.app.data.universe`: ticker sanitization, dedupe,
denylist/broken-data purging, `needs_sync()` staleness, and the
sync()-falls-back-to-a-static-seed-list path when the live fetch fails.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
import requests

import backend.app.data.universe as universe_mod
from backend.app.data.universe import (
    DENYLIST,
    UniverseManager,
    sanitize_ticker,
)
from backend.app.quant.data.parquet_manager import ParquetSyncManager


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

    def test_sync_keeps_previous_file_when_network_is_unavailable(
        self, manager, monkeypatch
    ):
        """F4: any source falling back keeps the previous file byte-for-byte."""
        monkeypatch.setattr(
            pd,
            "read_html",
            lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("no network in this sandbox")
            ),
        )
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        _write_parquet(manager.data_dir / "AAPL.parquet")
        previous = json.dumps(
            {"symbols": ["AAPL"], "names": {"AAPL": "Apple Inc."}, "synced_at": "x"}
        )
        manager.config_path.write_text(previous)

        result = manager.sync().as_dict()

        assert result["kept_previous"] is True
        assert set(result["source_status"].values()) == {"fallback"}
        assert manager.config_path.read_text() == previous


class TestReadRoundTrip:
    def test_read_after_live_sync_matches(self, manager, monkeypatch):
        sp = pd.DataFrame(
            {
                "Symbol": [f"Z{i:03d}" for i in range(480)] + ["AAPL"],
                "Security": [f"Zeta {i}" for i in range(480)] + ["Apple Inc."],
            }
        )
        ndx = [
            pd.DataFrame(
                {
                    "Company": [f"Nova {i}" for i in range(95)],
                    "Ticker": [f"N{i:03d}" for i in range(95)],
                }
            )
        ]

        def fake(url):
            return [sp] if url == universe_mod._SP500_WIKI_URL else ndx

        monkeypatch.setattr(UniverseManager, "_fetch_tables", staticmethod(fake))

        def fake_sync_ticker(self, ticker, now=None):
            _write_parquet(Path(self.data_dir) / f"{ticker}.parquet")

        monkeypatch.setattr(ParquetSyncManager, "sync_ticker", fake_sync_ticker)
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        _write_parquet(manager.data_dir / "AAPL.parquet")

        synced = manager.sync()
        read_back = manager.read()

        assert synced.as_dict()["kept_previous"] is False
        assert read_back is not None
        assert read_back.symbols == synced.symbols
        assert manager.config_path.exists()

    def test_read_returns_none_when_never_synced(self, manager):
        assert manager.read() is None


# ---------------------------------------------------------------------------
# Cycle 01 - delisted tickers (stories C-2 / C-4)
#
# `DENY_ENTRIES` / `DenyEntry` are read off the module at call time (not
# imported at the top of this file) so that, before they exist, only these
# tests fail - the rest of the module still collects and runs.
# ---------------------------------------------------------------------------

VALID_KINDS = {"rename", "stock_merger", "cash_buyout", "broken_data"}

#: ticker -> (kind, since, successor), as agreed at Gate 1.
EXPECTED_DENY_ENTRIES = {
    "EA": ("cash_buyout", date(2026, 8, 4), None),
    "KVUE": ("stock_merger", date(2026, 11, 2), "KMB"),
    "WBD": ("cash_buyout", date(2026, 10, 6), None),
    "AVB": ("stock_merger", date(2026, 8, 18), "VMRK"),
    "EQR": ("rename", date(2026, 8, 18), "VMRK"),
    "BK": ("rename", date(2026, 5, 21), "BNY"),
    "MMC": ("rename", date(2026, 1, 14), "MRSH"),
    "CTRA": ("stock_merger", date(2026, 5, 7), "DVN"),
    "K": ("cash_buyout", date(2025, 12, 11), None),
    "WBA": ("cash_buyout", date(2025, 8, 28), None),
}
DENIED_TICKERS = tuple(EXPECTED_DENY_ENTRIES)
NEW_LIVE_TICKERS = ("VMRK", "BNY", "MRSH")


def _deny_entries():
    return universe_mod.DENY_ENTRIES


class TestC2DenyEntries:
    @pytest.mark.parametrize("ticker", DENIED_TICKERS)
    def test_c2_deny_entry_matches_gate1_decision(self, ticker):
        kind, since, successor = EXPECTED_DENY_ENTRIES[ticker]
        entry = _deny_entries()[ticker]
        assert entry.kind == kind
        assert entry.since == since
        assert entry.successor == successor

    def test_c2_deny_entries_cover_exactly_the_agreed_tickers(self):
        assert set(_deny_entries()) == set(DENIED_TICKERS)

    def test_c2_denylist_is_derived_from_deny_entries(self):
        assert isinstance(universe_mod.DENYLIST, frozenset)
        assert universe_mod.DENYLIST == frozenset(_deny_entries())

    @pytest.mark.parametrize("ticker", DENIED_TICKERS)
    def test_c2_every_deny_entry_is_well_formed(self, ticker):
        entry = _deny_entries()[ticker]
        assert isinstance(entry, universe_mod.DenyEntry)
        assert ticker == sanitize_ticker(ticker), "keys must be sanitized"
        assert entry.kind in VALID_KINDS
        assert entry.since is None or isinstance(entry.since, date)
        assert isinstance(entry.reason, str) and entry.reason.strip()
        if entry.kind in ("rename", "stock_merger"):
            assert entry.successor, f"{ticker}: {entry.kind} needs a successor"
        if entry.kind == "cash_buyout":
            assert entry.successor is None
        if entry.successor is not None:
            assert entry.successor == sanitize_ticker(entry.successor)
            assert entry.successor not in universe_mod.DENYLIST
            assert entry.successor != ticker

    def test_c2_delisted_entries_all_carry_a_date(self):
        for ticker, entry in _deny_entries().items():
            if entry.kind != "broken_data":
                assert entry.since is not None, ticker

    def test_c2_deny_entry_is_frozen(self):
        entry = _deny_entries()["KVUE"]
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.kind = "broken_data"

    def test_c2_successor_defaults_to_none(self):
        entry = universe_mod.DenyEntry(kind="broken_data", since=None, reason="x")
        assert entry.successor is None


class TestC2StaticFallbacks:
    @pytest.mark.parametrize(
        "name", ["_STATIC_SP500_FALLBACK", "_STATIC_NASDAQ100_FALLBACK"]
    )
    def test_c2_fallback_contains_no_denied_ticker(self, name):
        fallback = set(getattr(universe_mod, name))
        assert fallback.isdisjoint(DENIED_TICKERS), sorted(
            fallback & set(DENIED_TICKERS)
        )

    @pytest.mark.parametrize("ticker", NEW_LIVE_TICKERS)
    def test_c2_c4_sp500_fallback_contains_successor(self, ticker):
        assert ticker in universe_mod._STATIC_SP500_FALLBACK

    def test_c2_every_successor_is_in_some_fallback(self):
        fallback = set(universe_mod._STATIC_SP500_FALLBACK) | set(
            universe_mod._STATIC_NASDAQ100_FALLBACK
        )
        successors = {e.successor for e in _deny_entries().values() if e.successor}
        assert successors <= fallback, sorted(successors - fallback)


class TestC2OfflineSyncCannotResurrect:
    def test_c2_offline_sync_excludes_denied_even_with_data_on_disk(
        self, manager, monkeypatch
    ):
        monkeypatch.setattr(
            pd,
            "read_html",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        for ticker in DENIED_TICKERS + NEW_LIVE_TICKERS + ("AAPL",):
            _write_parquet(manager.data_dir / f"{ticker}.parquet")

        result = manager.sync()

        assert set(result.symbols).isdisjoint(DENIED_TICKERS)
        assert set(NEW_LIVE_TICKERS) <= set(result.symbols)
        # Non-destructive: denied tickers' files are still on disk.
        for ticker in DENIED_TICKERS:
            assert (manager.data_dir / f"{ticker}.parquet").exists()

    def test_c2_live_sync_listing_denied_tickers_still_excludes_them(self, manager):
        manager.fetch_sp500_constituents = lambda: list(DENIED_TICKERS) + ["AAPL"]
        manager.fetch_nasdaq100_constituents = lambda: ["kvue", "wbd", "ea"]
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        for ticker in DENIED_TICKERS + ("AAPL",):
            _write_parquet(manager.data_dir / f"{ticker}.parquet")

        result = manager.sync()

        assert result.symbols == ["AAPL"]

    def test_c2_live_sync_warns_naming_each_denied_symbol(self, manager, caplog):
        """A live constituent list that still carries a denied symbol means
        the source is out of date; sync excludes it and logs a WARNING."""
        manager.fetch_sp500_constituents = lambda: ["AAPL", "EQR"]
        manager.fetch_nasdaq100_constituents = lambda: ["kvue"]
        manager.data_dir.mkdir(parents=True, exist_ok=True)
        for ticker in ("AAPL", "EQR", "KVUE"):
            _write_parquet(manager.data_dir / f"{ticker}.parquet")

        with caplog.at_level("WARNING", logger=universe_mod.__name__):
            result = manager.sync()

        assert result.symbols == ["AAPL"]
        warnings = [
            r.getMessage()
            for r in caplog.records
            if r.name == universe_mod.__name__ and r.levelname == "WARNING"
        ]
        for ticker in ("EQR", "KVUE"):
            assert any(ticker in msg for msg in warnings), (ticker, warnings)
        assert not any("AAPL" in msg for msg in warnings), warnings


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
