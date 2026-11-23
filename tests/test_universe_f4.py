"""
Batch 02 F4: one ticker list (`config/universe.json`), refreshed monthly.

Agreed interface (Gate 1 + facilitator, 2026-11-23):
- `deps.load_watchlist` reads `Settings.universe_path` (default
  `config/universe.json`, `symbols` list); `watchlist_path`,
  `WATCHLIST_PATH` and `config/watchlist.txt` are gone.
- `UniverseManager.sync()` result (and `as_dict()`) gains
  `source_status` {"sp500"/"nasdaq100": "live"|"fallback"}, `changes`
  {"added", "removed", "renamed": [{"old", "new"}]} vs the previous file
  and `kept_previous`. Any fallback, or a live count below 450 (S&P) / 90
  (Nasdaq-100), keeps the previous file byte-for-byte (no partial write).
- Renames: a removed and an added symbol whose Wikipedia company name
  matches (case/punctuation-normalised) are reported as renamed, not as
  added/removed. The file stores `names` {symbol: company} so next month
  can match, and `last_changes` for the weekly report (F5).
- Added (and renamed-to) names get history via
  `ParquetSyncManager.sync_ticker` in the manager's `data_dir`; removed
  names are flagged, their parquet is never touched.
- Scheduler job `universe_monthly`: 1st of the month 07:00 Europe/London,
  coalesce, misfire grace >= 6h. The old universe-only daily job code is
  removed.

Network is mocked at `UniverseManager._fetch_tables`; files live in
`tmp_path`. The repo-state class at the bottom reads the committed files.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow.parquet as pq
import pytest
import requests
from fastapi.testclient import TestClient

import backend.app.data.universe as universe_mod
from backend.app.config import Settings, get_settings
from backend.app.data.universe import DENYLIST, UniverseManager
from backend.app.main import app
from backend.app.quant.data.parquet_manager import ParquetSyncManager, SyncResult

REPO = Path(__file__).resolve().parent.parent
UK = ZoneInfo("Europe/London")
MIN_GRACE = 6 * 3600
SP_URL = universe_mod._SP500_WIKI_URL
NDX_URL = universe_mod._NASDAQ100_WIKI_URL

SP_BASE = [(f"Z{i:03d}", f"Zeta {i} Holdings") for i in range(480)]
NDX_BASE = [(f"N{i:03d}", f"Nova {i} Systems") for i in range(95)]
PREV_EXTRA = {"AAPL": "Apple Inc.", "FI": "Fiserv, Inc.", "OLDCO": "Old Company Corp"}
SP_NEW_EXTRA = [
    ("AAPL", "Apple Inc."),
    ("BRK.B", "Berkshire Hathaway"),
    ("FISV", "Fiserv Inc"),
    ("NEWCO", "New Company Group"),
]


def _template_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "_template.parquet"
    if not path.exists():
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        pd.DataFrame(
            {c: [1.0] * 5 for c in ("Open", "High", "Low", "Close")}
            | {"Volume": [1_000] * 5},
            index=idx,
        ).to_parquet(path)
    return path.read_bytes()


def _sp_table(rows) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Symbol": [s for s, _ in rows],
            "Security": [n for _, n in rows],
            "GICS Sector": ["Industrials"] * len(rows),
        }
    )


def _ndx_tables(rows) -> list[pd.DataFrame]:
    # The real page carries other tables before the constituents table.
    return [
        pd.DataFrame({"Year": [2025], "Change": ["x"]}),
        pd.DataFrame({"Company": [n for _, n in rows], "Ticker": [s for s, _ in rows]}),
    ]


def _fake_wiki(monkeypatch, sp_rows=None, ndx_rows=None, fail=()):
    sp_rows = SP_BASE + SP_NEW_EXTRA if sp_rows is None else sp_rows
    ndx_rows = NDX_BASE + [("AAPL", "Apple Inc.")] if ndx_rows is None else ndx_rows

    def fake(url):
        if url == SP_URL:
            if "sp500" in fail:
                raise requests.HTTPError("403 Client Error: Forbidden")
            if "sp500_shape" in fail:
                return [pd.DataFrame({"Ticker symbol": ["AAPL"]})]
            return [_sp_table(sp_rows)]
        if url == NDX_URL:
            if "nasdaq100" in fail:
                raise requests.ConnectionError("network down")
            if "nasdaq100_shape" in fail:
                return [pd.DataFrame({"Year": [2025]})]
            return _ndx_tables(ndx_rows)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(UniverseManager, "_fetch_tables", staticmethod(fake))


@pytest.fixture(autouse=True)
def _guard_repo_files(monkeypatch, tmp_path):
    """Nothing in this module may write config/ or data/raw in the repo."""
    real_write = getattr(UniverseManager, "_write", None)
    if real_write is not None:

        def guarded(self, *a, **k):
            assert (
                tmp_path in Path(self.config_path).resolve().parents
            ), f"_write to {self.config_path}, outside tmp_path"
            return real_write(self, *a, **k)

        monkeypatch.setattr(UniverseManager, "_write", guarded)


@pytest.fixture
def history_calls(monkeypatch, tmp_path):
    """Fake `ParquetSyncManager.sync_ticker`: records (data_dir, ticker) and
    writes a 5-row parquet, refusing to write outside tmp_path."""
    calls: list[tuple[Path, str]] = []
    blob = _template_bytes(tmp_path)

    def fake(self, ticker, now=None):
        calls.append((Path(self.data_dir), ticker))
        assert tmp_path in Path(self.data_dir).resolve().parents
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        (Path(self.data_dir) / f"{ticker}.parquet").write_bytes(blob)
        return SyncResult(ticker=ticker, status="updated", rows_added=5)

    monkeypatch.setattr(ParquetSyncManager, "sync_ticker", fake)
    return calls


def _previous(tmp_path: Path, with_names: bool = True) -> UniverseManager:
    """A manager whose previous universe.json holds SP_BASE + NDX_BASE +
    AAPL, FI, OLDCO, each with a parquet in tmp_path/raw."""
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    blob = _template_bytes(tmp_path)
    names = dict(SP_BASE) | dict(NDX_BASE) | PREV_EXTRA
    for sym in names:
        (raw / f"{sym}.parquet").write_bytes(blob)
    payload = {
        "symbols": sorted(names),
        "synced_at": "2026-10-01T06:00:00+00:00",
        "source_counts": {"sp500": 483, "nasdaq100": 96},
    }
    if with_names:
        payload["names"] = names
    cfg = tmp_path / "universe.json"
    cfg.write_text(json.dumps(payload, indent=2))
    return UniverseManager(config_path=cfg, data_dir=raw)


def _as_dict(result) -> dict:
    return result.as_dict()


# ------------------------------------------------------------------ sync


class TestF4SyncChanges:
    def test_f4_live_sync_reports_source_status_live(
        self, tmp_path, monkeypatch, history_calls
    ):
        _fake_wiki(monkeypatch)
        result = _as_dict(_previous(tmp_path).sync())
        assert result["source_status"] == {"sp500": "live", "nasdaq100": "live"}
        assert result["kept_previous"] is False

    def test_f4_changes_report_added_removed_and_fi_to_fisv_rename(
        self, tmp_path, monkeypatch, history_calls
    ):
        _fake_wiki(monkeypatch)
        changes = _as_dict(_previous(tmp_path).sync())["changes"]
        assert sorted(changes["added"]) == ["BRK-B", "NEWCO"]
        assert changes["removed"] == ["OLDCO"]
        assert changes["renamed"] == [{"old": "FI", "new": "FISV"}]

    def test_f4_legacy_file_without_names_still_diffs(
        self, tmp_path, monkeypatch, history_calls
    ):
        _fake_wiki(monkeypatch)
        changes = _as_dict(_previous(tmp_path, with_names=False).sync())["changes"]
        assert sorted(changes["added"]) == ["BRK-B", "FISV", "NEWCO"]
        assert sorted(changes["removed"]) == ["FI", "OLDCO"]
        assert changes["renamed"] == []

    def test_f4_written_file_has_symbols_names_and_last_changes(
        self, tmp_path, monkeypatch, history_calls
    ):
        _fake_wiki(monkeypatch)
        manager = _previous(tmp_path)
        result = _as_dict(manager.sync())
        on_disk = json.loads(manager.config_path.read_text())
        assert on_disk["symbols"] == result["symbols"]
        assert {"BRK-B", "NEWCO", "FISV", "AAPL"} <= set(on_disk["symbols"])
        assert not {"FI", "OLDCO"} & set(on_disk["symbols"])
        assert on_disk["names"]["FISV"] == "Fiserv Inc"
        assert on_disk["names"]["BRK-B"] == "Berkshire Hathaway"
        assert on_disk["last_changes"]["added"] == result["changes"]["added"]
        assert on_disk["last_changes"]["removed"] == result["changes"]["removed"]
        assert on_disk["last_changes"]["renamed"] == result["changes"]["renamed"]
        assert manager.read().symbols == result["symbols"]

    def test_f4_added_and_renamed_names_get_history_in_manager_data_dir(
        self, tmp_path, monkeypatch, history_calls
    ):
        _fake_wiki(monkeypatch)
        manager = _previous(tmp_path)
        manager.sync()
        assert sorted(t for _, t in history_calls) == ["BRK-B", "FISV", "NEWCO"]
        assert {d.resolve() for d, _ in history_calls} == {manager.data_dir.resolve()}

    def test_f4_removed_names_are_flagged_never_deleted(
        self, tmp_path, monkeypatch, history_calls
    ):
        _fake_wiki(monkeypatch)
        manager = _previous(tmp_path)
        blob = _template_bytes(tmp_path)
        manager.sync()
        for sym in ("OLDCO", "FI"):
            assert (manager.data_dir / f"{sym}.parquet").read_bytes() == blob

    def test_f4_unchanged_lists_report_no_changes_and_fetch_nothing(
        self, tmp_path, monkeypatch, history_calls
    ):
        manager = _previous(tmp_path)
        prev = json.loads(manager.config_path.read_text())["names"]
        sp = [(s, n) for s, n in prev.items() if not s.startswith("N")]
        _fake_wiki(monkeypatch, sp_rows=sp)
        changes = _as_dict(manager.sync())["changes"]
        assert changes == {"added": [], "removed": [], "renamed": []}
        assert history_calls == []


class TestF4KeepPrevious:
    @pytest.mark.parametrize(
        "fail, fell_back",
        [
            (("sp500",), {"sp500"}),
            (("nasdaq100",), {"nasdaq100"}),
            (("sp500", "nasdaq100"), {"sp500", "nasdaq100"}),
            (("sp500_shape",), {"sp500"}),
            (("nasdaq100_shape",), {"nasdaq100"}),
        ],
    )
    def test_f4_any_fallback_keeps_previous_file_unchanged(
        self, tmp_path, monkeypatch, history_calls, caplog, fail, fell_back
    ):
        manager = _previous(tmp_path)
        before = manager.config_path.read_bytes()
        _fake_wiki(monkeypatch, fail=fail)
        with caplog.at_level(logging.WARNING):
            result = _as_dict(manager.sync())
        assert manager.config_path.read_bytes() == before
        assert result["kept_previous"] is True
        assert {k for k, v in result["source_status"].items() if v == "fallback"} == (
            fell_back
        )
        assert history_calls == []
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "_template.parquet",
            "raw",
            "universe.json",
        ], "no partial or temp file left behind"
        warned = " ".join(r.getMessage().lower() for r in caplog.records)
        assert "previous" in warned or "kept" in warned or "keep" in warned

    @pytest.mark.parametrize(
        "n_sp, n_ndx, kept",
        [(449, 95, True), (480, 89, True), (450, 95, False), (480, 90, False)],
    )
    def test_f4_live_count_thresholds(
        self, tmp_path, monkeypatch, history_calls, n_sp, n_ndx, kept
    ):
        manager = _previous(tmp_path)
        before = manager.config_path.read_bytes()
        _fake_wiki(monkeypatch, sp_rows=SP_BASE[:n_sp], ndx_rows=NDX_BASE[:n_ndx])
        result = _as_dict(manager.sync())
        assert result["source_status"] == {"sp500": "live", "nasdaq100": "live"}
        assert result["kept_previous"] is kept
        assert (manager.config_path.read_bytes() == before) is kept

    def test_f4_fallback_with_no_previous_file_writes_nothing(
        self, tmp_path, monkeypatch, history_calls
    ):
        manager = UniverseManager(
            config_path=tmp_path / "universe.json", data_dir=tmp_path / "raw"
        )
        _fake_wiki(monkeypatch, fail=("nasdaq100",))
        result = _as_dict(manager.sync())
        assert result["kept_previous"] is True
        assert not manager.config_path.exists()


# -------------------------------------------------------- load_watchlist


def _settings(tmp_path, symbols=None, **kw) -> Settings:
    path = tmp_path / "universe.json"
    if symbols is not None:
        path.write_text(json.dumps({"symbols": symbols, "synced_at": "x"}))
    kw.setdefault("scheduler_enabled", False)
    return Settings(
        data_dir=tmp_path / "raw", allow_downloads=False, universe_path=path, **kw
    )


class TestF4LoadWatchlist:
    def test_f4_settings_has_universe_path_and_no_watchlist_path(self):
        fields = {f.name for f in dataclasses.fields(Settings)}
        assert "watchlist_path" not in fields
        assert Settings().universe_path == Path("config/universe.json")

    def test_f4_watchlist_path_env_is_ignored(self, monkeypatch):
        monkeypatch.setenv("WATCHLIST_PATH", "/nope/watchlist.txt")
        get_settings.cache_clear()
        try:
            settings = get_settings()
            assert not hasattr(settings, "watchlist_path")
            assert settings.universe_path == Path("config/universe.json")
        finally:
            get_settings.cache_clear()

    def test_f4_load_watchlist_reads_universe_json(self, tmp_path):
        from backend.app.api.deps import load_watchlist

        settings = _settings(tmp_path, ["AAPL", "BRK-B", "MSFT"])
        assert load_watchlist(settings) == ["AAPL", "BRK-B", "MSFT"]

    def test_f4_load_watchlist_drops_denied_symbols(self, tmp_path):
        from backend.app.api.deps import load_watchlist

        denied = sorted(DENYLIST)[:3]
        settings = _settings(tmp_path, ["AAPL", *denied, "MSFT"])
        assert load_watchlist(settings) == ["AAPL", "MSFT"]

    def test_f4_load_watchlist_caps_and_reports_overflow(self, tmp_path):
        from backend.app.api.deps import load_watchlist, watchlist_overflow

        settings = _settings(
            tmp_path, [f"T{i:03d}" for i in range(50)], screener_max_tickers=10
        )
        assert len(load_watchlist(settings)) == 10
        assert watchlist_overflow(settings) == 40

    @pytest.mark.parametrize("content", [None, "{not json", '{"synced_at": "x"}'])
    def test_f4_missing_or_malformed_universe_is_empty(self, tmp_path, content):
        from backend.app.api.deps import load_watchlist, watchlist_overflow

        settings = _settings(tmp_path)
        if content is not None:
            settings.universe_path.write_text(content)
        assert load_watchlist(settings) == []
        assert watchlist_overflow(settings) == 0


# -------------------------------------------------------------- schedule


def _jobs(settings) -> dict:
    from backend.app.scheduler import build_jobs

    return {job.id: job for job in build_jobs(settings)}


class TestF4Schedule:
    @pytest.mark.parametrize(
        "after, expected",
        [
            (datetime(2026, 11, 23, 9), datetime(2026, 12, 1, 7)),
            (datetime(2026, 12, 1, 7, 1), datetime(2027, 1, 1, 7)),
            # UK clocks go forward Sun 28 Mar 2027: still 07:00 local (BST).
            (datetime(2027, 3, 15, 12), datetime(2027, 4, 1, 7)),
        ],
    )
    def test_f4_universe_monthly_fires_1st_at_0700_uk(self, tmp_path, after, expected):
        trigger = _jobs(_settings(tmp_path))["universe_monthly"].trigger
        assert str(trigger.timezone) == "Europe/London"
        fire = trigger.get_next_fire_time(None, after.replace(tzinfo=UK))
        assert fire.astimezone(UK).replace(tzinfo=None) == expected

    def test_f4_universe_monthly_coalesces_with_grace(self, tmp_path):
        job = _jobs(_settings(tmp_path))["universe_monthly"]
        assert job.coalesce is True
        assert job.misfire_grace_time >= MIN_GRACE

    def test_f4_universe_monthly_syncs_settings_paths(self, tmp_path, monkeypatch):
        seen = []
        monkeypatch.setattr(
            UniverseManager,
            "sync",
            lambda self: seen.append((Path(self.config_path), Path(self.data_dir))),
        )
        settings = _settings(tmp_path)
        _jobs(settings)["universe_monthly"].func()
        assert seen == [(settings.universe_path, settings.data_dir)]

    def test_f4_universe_monthly_failure_is_logged_not_raised(
        self, tmp_path, monkeypatch, caplog
    ):
        def boom(self):
            raise RuntimeError("wikipedia down")

        monkeypatch.setattr(UniverseManager, "sync", boom)
        with caplog.at_level(logging.ERROR):
            _jobs(_settings(tmp_path))["universe_monthly"].func()
        assert "universe_monthly" in caplog.text

    def test_f4_start_registers_universe_monthly_not_daily(self, tmp_path, monkeypatch):
        from fastapi import FastAPI

        from backend.app.scheduler import start_scheduler

        class Fake:
            def __init__(self):
                self.jobs = {}

            def add_job(self, func, trigger=None, id=None, **kw):
                self.jobs[id] = kw

        fake = Fake()
        monkeypatch.setattr(universe_mod, "_scheduler", fake)
        start_scheduler(FastAPI(), _settings(tmp_path, scheduler_enabled=True))
        assert "universe_monthly" in fake.jobs
        assert fake.jobs["universe_monthly"].get("coalesce") is True
        assert "universe_daily_sync" not in fake.jobs

    def test_f4_old_universe_daily_job_code_removed(self):
        assert not hasattr(universe_mod, "check_and_schedule_startup_sync")
        assert not hasattr(universe_mod, "register_universe_startup")
        main_src = (REPO / "backend" / "app" / "main.py").read_text()
        assert "check_and_schedule_startup_sync" not in main_src


# ------------------------------------------------------------------- API


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.pop(get_settings, None)


class TestF4Api:
    def test_f4_post_sync_uses_settings_paths_and_returns_changes(
        self, tmp_path, monkeypatch, history_calls, client
    ):
        manager = _previous(tmp_path)
        _fake_wiki(monkeypatch)
        settings = _settings(tmp_path)
        assert settings.universe_path == manager.config_path
        settings = dataclasses.replace(settings, data_dir=manager.data_dir)
        app.dependency_overrides[get_settings] = lambda: settings
        body = client.post("/api/v1/universe/sync").json()
        assert body["source_status"] == {"sp500": "live", "nasdaq100": "live"}
        assert body["changes"]["renamed"] == [{"old": "FI", "new": "FISV"}]
        assert body["kept_previous"] is False
        assert "FISV" in json.loads(manager.config_path.read_text())["symbols"]

    def test_f4_get_symbols_does_not_resync_within_the_month(
        self, tmp_path, monkeypatch, client
    ):
        stamp = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        path = tmp_path / "universe.json"
        path.write_text(
            json.dumps(
                {
                    "symbols": ["AAPL", "MSFT"],
                    "synced_at": stamp,
                    "source_counts": {"sp500": 503, "nasdaq100": 101},
                }
            )
        )

        def no_sync(self):
            raise AssertionError("GET must not trigger a live sync mid-month")

        monkeypatch.setattr(UniverseManager, "sync", no_sync)
        settings = _settings(tmp_path)
        app.dependency_overrides[get_settings] = lambda: settings
        response = client.get("/api/v1/universe/symbols")
        assert response.status_code == 200
        assert response.json()["symbols"] == ["AAPL", "MSFT"]


# ------------------------------------------------------ committed files


def _tracked(rel: str) -> bool:
    out = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return out.returncode == 0


NEW_MEMBERS = ("BRK-B", "BF-B", "SKYD", "HOOD", "RDDT", "IBKR", "VRT", "FISV")


class TestF4RepoState:
    def test_f4_watchlist_txt_is_gone(self):
        assert not (REPO / "config" / "watchlist.txt").exists()
        assert not _tracked("config/watchlist.txt")

    def test_f4_universe_json_is_committed(self):
        assert _tracked("config/universe.json")

    def test_f4_committed_universe_is_current(self):
        payload = json.loads((REPO / "config" / "universe.json").read_text())
        symbols = set(payload["symbols"])
        assert len(symbols) >= 500
        assert set(NEW_MEMBERS) <= symbols
        assert "FI" not in symbols
        assert not symbols & DENYLIST
        assert payload["source_status"] == {"sp500": "live", "nasdaq100": "live"}
        assert set(payload["names"]) >= symbols

    def test_f4_every_committed_symbol_has_tracked_history(self):
        payload = json.loads((REPO / "config" / "universe.json").read_text())
        tracked = set(
            subprocess.run(
                ["git", "ls-files", "data/raw"],
                cwd=REPO,
                capture_output=True,
                text=True,
            ).stdout.split()
        )
        missing = [
            s
            for s in payload["symbols"]
            if f"data/raw/{s}.parquet" not in tracked
            or pq.ParquetFile(REPO / "data" / "raw" / f"{s}.parquet").metadata.num_rows
            == 0
        ]
        assert missing == []

    @pytest.mark.parametrize(
        "rel",
        [".env.example", "README.md", "docs/api.md"],
    )
    def test_f4_docs_do_not_mention_watchlist_file(self, rel):
        text = (REPO / rel).read_text()
        assert "WATCHLIST_PATH" not in text
        assert "watchlist.txt" not in text

    def test_f4_code_does_not_mention_watchlist_file(self):
        hits = subprocess.run(
            [
                "git",
                "grep",
                "-n",
                "-e",
                "watchlist.txt",
                "-e",
                "WATCHLIST_PATH",
                "-e",
                "watchlist_path",
                "--",
                "backend",
                "scripts",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
        ).stdout
        assert hits == ""
