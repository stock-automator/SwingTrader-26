"""Batch 02 F3 part B-1: in-repo scheduling and the cached daily brief.

Agreed interface (Gate 1): `backend/app/scheduler.py` exposes
`build_jobs(settings) -> list[JobSpec]` (each spec: `id`, zero-arg `func`,
`trigger`, `coalesce`, `misfire_grace_time`) and `start_scheduler(app,
settings)`, which reuses the process scheduler in
`backend.app.data.universe._scheduler`. The brief cache is
`<data_dir>/reports/daily_brief.json`. Nothing here touches the network,
the real scheduler or `data/raw/`.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import stat
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.main import app
from backend.app.reports.daily import BriefSetup, DailyBrief

REPO = Path(__file__).resolve().parent.parent
UK = ZoneInfo("Europe/London")
MIN_GRACE = 6 * 3600
CANNED_TEXT = "No trades today: canned.\n\nResearch only, paper trading."


def _settings(tmp_path, **kw) -> Settings:
    data = tmp_path / "raw"
    data.mkdir(exist_ok=True)
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"symbols": ["AAA", "BBB"], "names": {}}))
    kw.setdefault("scheduler_enabled", False)
    return Settings(
        data_dir=data,
        allow_downloads=False,
        universe_path=universe,
        journal_path=tmp_path / "trades.csv",
        alert_config_path=tmp_path / "alerts_channels.json",
        **kw,
    )


def _canned_brief() -> DailyBrief:
    return DailyBrief(
        as_of=date(2026, 11, 17),
        last_session=date(2026, 11, 17),
        regime="BULL",
        stale=False,
        text=CANNED_TEXT,
        setups=[
            BriefSetup(
                ticker="AAA",
                strategy="donchian",
                entry=100.0,
                stop=95.0,
                target=115.0,
                shares=4,
                risk_usd=20.0,
                risk_gbp=16.0,
                reason="breakout",
            )
        ],
    )


class _Builder:
    def __init__(self):
        self.calls = 0

    def __call__(self, settings, *a, **k):
        self.calls += 1
        return _canned_brief()


@pytest.fixture
def builder(monkeypatch):
    from backend.app.reports import daily

    fake = _Builder()
    monkeypatch.setattr(daily, "build_daily_brief", fake)
    return fake


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.pop(get_settings, None)


def _use(settings):
    app.dependency_overrides[get_settings] = lambda: settings


def _jobs(settings) -> dict:
    from backend.app.scheduler import build_jobs

    return {job.id: job for job in build_jobs(settings)}


def _cache(settings) -> Path:
    return Path(settings.data_dir) / "reports" / "daily_brief.json"


class _Resp:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Bad Request")


# --------------------------------------------------------------- the jobs


class TestF3bJobs:
    def test_f3b_build_jobs_has_sync_and_brief(self, tmp_path):
        assert {"data_sync", "daily_brief"} <= set(_jobs(_settings(tmp_path)))

    @pytest.mark.parametrize(
        "job_id, hh, mm", [("data_sync", 7, 30), ("daily_brief", 8, 0)]
    )
    def test_f3b_job_fires_weekdays_uk_time(self, tmp_path, job_id, hh, mm):
        trigger = _jobs(_settings(tmp_path))[job_id].trigger
        assert str(trigger.timezone) == "Europe/London"
        # Saturday 21 Nov 2026 noon -> next fire is Monday 23 Nov.
        fire = trigger.get_next_fire_time(None, datetime(2026, 11, 21, 12, tzinfo=UK))
        assert fire.astimezone(UK).replace(tzinfo=None) == datetime(
            2026, 11, 23, hh, mm
        )
        fires, prev = [], datetime(2026, 11, 22, 23, tzinfo=UK)
        for _ in range(6):
            prev = trigger.get_next_fire_time(None, prev + timedelta(minutes=1))
            fires.append(prev.astimezone(UK))
        assert [f.weekday() for f in fires] == [0, 1, 2, 3, 4, 0]
        assert all((f.hour, f.minute) == (hh, mm) for f in fires)

    def test_f3b_brief_time_holds_across_uk_dst_change(self, tmp_path):
        trigger = _jobs(_settings(tmp_path))["daily_brief"].trigger
        # UK clocks go back Sun 25 Oct 2026; Monday is still 08:00 local.
        fire = trigger.get_next_fire_time(None, datetime(2026, 10, 24, 9, tzinfo=UK))
        local = fire.astimezone(UK)
        assert (local.date(), local.hour, local.minute) == (date(2026, 10, 26), 8, 0)

    @pytest.mark.parametrize("job_id", ["data_sync", "daily_brief"])
    def test_f3b_missed_job_runs_once_after_wake(self, tmp_path, job_id):
        job = _jobs(_settings(tmp_path))[job_id]
        assert job.coalesce is True
        assert job.misfire_grace_time is not None
        assert job.misfire_grace_time >= MIN_GRACE

    def test_f3b_data_sync_job_syncs_watchlist_fx_and_spy(self, tmp_path, monkeypatch):
        from backend.app.quant.data.parquet_manager import ParquetSyncManager

        seen = []
        monkeypatch.setattr(
            ParquetSyncManager,
            "sync_universe",
            lambda self, tickers, now=None: seen.append(
                (Path(self.data_dir), list(tickers))
            )
            or [],
        )
        monkeypatch.setattr(
            ParquetSyncManager,
            "sync_ticker",
            lambda self, t, now=None: seen.append((Path(self.data_dir), [t])),
        )
        settings = _settings(tmp_path)
        _jobs(settings)["data_sync"].func()
        tickers = {t for _, batch in seen for t in batch}
        assert {"AAA", "BBB", "GBPUSD=X", "SPY"} <= tickers
        assert {d for d, _ in seen} == {Path(settings.data_dir)}

    def test_f3b_brief_job_caches_and_sends_when_configured(
        self, tmp_path, builder, monkeypatch
    ):
        posts = []
        monkeypatch.setattr(
            requests, "post", lambda url, **k: posts.append(k) or _Resp()
        )
        settings = _settings(tmp_path, telegram_bot_token="T", telegram_chat_id="1")
        _jobs(settings)["daily_brief"].func()
        assert builder.calls == 1
        cached = json.loads(_cache(settings).read_text())
        assert cached["text"] == CANNED_TEXT and cached["computed_at"]
        assert len(posts) == 1 and posts[0]["json"]["text"] == CANNED_TEXT

    def test_f3b_brief_job_caches_without_telegram(
        self, tmp_path, builder, monkeypatch
    ):
        monkeypatch.setattr(
            requests, "post", lambda *a, **k: pytest.fail("must not send")
        )
        settings = _settings(tmp_path)
        _jobs(settings)["daily_brief"].func()
        assert _cache(settings).exists()

    def test_f3b_brief_job_survives_telegram_400(self, tmp_path, builder, monkeypatch):
        monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(400))
        settings = _settings(tmp_path, telegram_bot_token="T", telegram_chat_id="1")
        _jobs(settings)["daily_brief"].func()  # no raise
        assert _cache(settings).exists()


# --------------------------------------------------------- start / disable


class _FakeScheduler:
    def __init__(self):
        self.jobs, self.funcs, self.started = {}, {}, False

    def add_job(self, func, trigger=None, id=None, **kw):
        self.jobs[id] = kw
        self.funcs[id] = func

    def start(self):
        self.started = True


class TestF3bStart:
    def test_f3b_env_flag_default_true_and_false(self, monkeypatch):
        monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
        get_settings.cache_clear()
        try:
            assert get_settings().scheduler_enabled is True
            monkeypatch.setenv("SCHEDULER_ENABLED", "false")
            get_settings.cache_clear()
            assert get_settings().scheduler_enabled is False
        finally:
            get_settings.cache_clear()

    def test_f3b_start_registers_jobs_on_the_shared_scheduler(
        self, tmp_path, monkeypatch
    ):
        from backend.app.data import universe
        from backend.app.scheduler import start_scheduler

        fake = _FakeScheduler()
        monkeypatch.setattr(universe, "_scheduler", fake)
        start_scheduler(FastAPI(), _settings(tmp_path, scheduler_enabled=True))
        assert {"data_sync", "daily_brief"} <= set(fake.jobs)
        for job_id in ("data_sync", "daily_brief"):
            assert fake.jobs[job_id].get("coalesce") is True
            assert fake.jobs[job_id].get("misfire_grace_time", 0) >= MIN_GRACE
            assert fake.jobs[job_id].get("replace_existing") is True

    def test_f3b_disabled_never_starts_a_scheduler(self, tmp_path, monkeypatch):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from backend.app.data import universe
        from backend.app.scheduler import start_scheduler

        starts = []
        monkeypatch.setattr(
            AsyncIOScheduler, "start", lambda self, *a, **k: starts.append(1)
        )
        monkeypatch.setattr(universe, "_scheduler", None)
        start_scheduler(FastAPI(), _settings(tmp_path, scheduler_enabled=False))
        assert starts == [] and universe._scheduler is None

    def test_f3b_app_startup_with_flag_off_starts_nothing(self, tmp_path, monkeypatch):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from backend.app.data import universe

        starts = []
        monkeypatch.setattr(
            AsyncIOScheduler, "start", lambda self, *a, **k: starts.append(1)
        )
        monkeypatch.setattr(universe, "_scheduler", None)
        monkeypatch.setenv("SCHEDULER_ENABLED", "false")
        get_settings.cache_clear()
        _use(_settings(tmp_path, scheduler_enabled=False))
        try:
            with TestClient(app):
                pass
        finally:
            app.dependency_overrides.pop(get_settings, None)
            get_settings.cache_clear()
        assert starts == [] and universe._scheduler is None


# ------------------------------------------------------------ the endpoints


class TestF3bCachedEndpoint:
    def test_f3b_get_daily_404_before_first_brief(self, tmp_path, builder, client):
        _use(_settings(tmp_path))
        res = client.get("/api/v1/reports/daily")
        assert res.status_code == 404, res.text
        assert "refresh" in res.json()["detail"].lower()
        assert builder.calls == 0

    def test_f3b_get_daily_serves_cache_without_building(
        self, tmp_path, builder, client
    ):
        settings = _settings(tmp_path)
        _jobs(settings)["daily_brief"].func()
        assert builder.calls == 1
        _use(settings)
        res = client.get("/api/v1/reports/daily")
        assert res.status_code == 200, res.text
        assert builder.calls == 1  # no synchronous scan
        body = res.json()
        assert {"as_of", "setups", "regime", "stale", "text", "computed_at"} <= set(
            body
        )
        assert body["text"] == CANNED_TEXT
        assert body["setups"][0]["ticker"] == "AAA"
        datetime.fromisoformat(body["computed_at"])

    def test_f3b_refresh_is_202_and_rebuilds_in_background(
        self, tmp_path, builder, client
    ):
        settings = _settings(tmp_path)
        _use(settings)
        res = client.post("/api/v1/reports/daily/refresh")
        assert res.status_code == 202, res.text
        # TestClient runs background tasks after the response.
        assert builder.calls == 1 and _cache(settings).exists()
        assert client.get("/api/v1/reports/daily").status_code == 200


# --------------------------------------------------------- day-trades line


class _FakeAccount(SimpleNamespace):
    pass


def _account(daytrade_count=1):
    return _FakeAccount(
        account_number="PA0000",
        status=SimpleNamespace(value="ACTIVE"),
        equity="1000",
        cash="1000",
        buying_power="1000",
        portfolio_value="1000",
        daytrade_count=daytrade_count,
        pattern_day_trader=False,
    )


@pytest.fixture
def offline_daily(monkeypatch):
    from backend.app.reports import daily

    monkeypatch.setattr(daily, "fetch_earnings_dates", lambda t, *a, **k: [])
    monkeypatch.setattr(daily, "fetch_stock_splits", lambda t, *a, **k: [], False)
    monkeypatch.setattr(daily, "latest_gbp_rate", lambda *a, **k: None)
    monkeypatch.setattr(daily, "_last_session", lambda s, now: date(2026, 11, 17))
    return daily


NOW = datetime(2026, 11, 18, 8, 0, tzinfo=UK)
DT_LINE = re.compile(r"Day trades used: (\d)/3 \(last 5 days\)")


class TestF3bDayTrades:
    def test_f3b_day_trades_line_from_alpaca(
        self, tmp_path, offline_daily, monkeypatch
    ):
        from alpaca.trading.client import TradingClient

        monkeypatch.setattr(TradingClient, "get_account", lambda self: _account(2))
        settings = _settings(tmp_path, alpaca_api_key="k", alpaca_api_secret="s")
        text = offline_daily.build_daily_brief(settings, now=NOW).text
        match = DT_LINE.search(text)
        assert match and match.group(1) == "2", text

    def test_f3b_no_day_trades_line_without_alpaca(
        self, tmp_path, offline_daily, monkeypatch
    ):
        from alpaca.trading.client import TradingClient

        monkeypatch.setattr(
            TradingClient, "get_account", lambda self: pytest.fail("not configured")
        )
        text = offline_daily.build_daily_brief(_settings(tmp_path), now=NOW).text
        assert "Day trades used" not in text

    @pytest.mark.parametrize(
        "exc", [requests.ConnectionError("down"), RuntimeError("500 from Alpaca")]
    )
    def test_f3b_day_trades_lookup_failure_omits_line(
        self, tmp_path, offline_daily, monkeypatch, exc
    ):
        from alpaca.trading.client import TradingClient

        def boom(self):
            raise exc

        monkeypatch.setattr(TradingClient, "get_account", boom)
        settings = _settings(tmp_path, alpaca_api_key="k", alpaca_api_secret="s")
        text = offline_daily.build_daily_brief(settings, now=NOW).text
        assert "Day trades used" not in text
        assert "Research only" in text


# --------------------------------------------------- repo files (deploy)


class TestF3bRepoFiles:
    @pytest.mark.parametrize("name", ["swing_setup_scan.py", "day_setup_scan.py"])
    def test_f3b_old_scan_scripts_retired(self, name):
        assert not (REPO / "scripts" / name).exists()

    def _plist(self) -> dict:
        path = REPO / "deploy" / "launchd" / "com.swingtrader.backend.plist"
        assert path.exists()
        with path.open("rb") as fh:
            return plistlib.load(fh)

    def test_f3b_plist_runs_uvicorn_on_localhost(self):
        plist = self._plist()
        args = plist["ProgramArguments"]
        joined = " ".join(args)
        assert "uvicorn" in joined and "backend.app.main:app" in args
        assert args[args.index("--host") + 1] == "127.0.0.1"
        assert "0.0.0.0" not in joined
        assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True
        assert plist["Label"] == "com.swingtrader.backend"

    def test_f3b_plist_working_dir_and_logs(self):
        plist = self._plist()
        workdir = plist["WorkingDirectory"]
        script = (REPO / "scripts" / "install_launchd.sh").read_text()
        # Either the repo itself, or a placeholder the installer fills in.
        assert Path(workdir) == REPO or workdir in script
        for key in ("StandardOutPath", "StandardErrorPath"):
            assert "/logs/" in plist[key]

    def test_f3b_plist_has_no_secrets(self):
        raw = (
            REPO / "deploy" / "launchd" / "com.swingtrader.backend.plist"
        ).read_text()
        assert not re.search(r"\d{8,10}:[A-Za-z0-9_-]{30,}", raw)  # Telegram
        assert not re.search(r"\b(PK|AK)[A-Z0-9]{16,}\b", raw)  # Alpaca key id
        assert not re.search(r"[A-Za-z0-9+]{40,}", raw)  # long secret blobs
        env = self._plist().get("EnvironmentVariables", {})
        for key in env:
            assert not re.search(r"TOKEN|SECRET|KEY|PASSWORD", key, re.I), key

    def test_f3b_install_script_is_executable(self):
        script = REPO / "scripts" / "install_launchd.sh"
        assert script.exists()
        assert script.stat().st_mode & stat.S_IXUSR
        assert os.access(script, os.X_OK)


# ------------------------------------------- rework 1: concurrency, order

# Interface notes for the developer:
# - one process-wide lock serialises brief builds + cache writes;
# - the scheduler module keeps `run_watchlist_sync` as a module-level name
#   (tests patch `backend.app.scheduler.run_watchlist_sync`);
# - "today's sync done" is tracked per `settings.data_dir` (tests use a fresh
#   tmp_path each) and a running sync is waited for by the brief job;
# - `start_scheduler(app, settings, now=None)`; the catch-up is a one-off
#   job with id "brief_catch_up" whose zero-arg func runs sync -> brief.


class _SlowBuilder:
    """Blocks inside the build until released; tracks overlapping builds."""

    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls = self.active = self.max_active = 0
        self._lock = threading.Lock()

    def __call__(self, settings, *a, **k):
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.entered.set()
        self.release.wait(5)
        with self._lock:
            self.active -= 1
        return _canned_brief()


@pytest.fixture
def slow_builder(monkeypatch):
    from backend.app.reports import daily

    fake = _SlowBuilder()
    monkeypatch.setattr(daily, "build_daily_brief", fake)
    return fake


def _thread(fn, *a):
    t = threading.Thread(target=fn, args=a, daemon=True)
    t.start()
    return t


@pytest.fixture
def sync_log(monkeypatch):
    """Record sync calls; the daily builder also logs into the same list."""
    from backend.app.reports import daily

    log: list[str] = []
    monkeypatch.setattr(
        "backend.app.scheduler.run_watchlist_sync", lambda s: log.append("sync")
    )

    def build(settings, *a, **k):
        log.append("build")
        return _canned_brief()

    monkeypatch.setattr(daily, "build_daily_brief", build)
    return log


class TestF3bConcurrency:
    def test_f3b_refresh_while_running_is_already_running(self, tmp_path, slow_builder):
        settings = _settings(tmp_path)
        _use(settings)
        try:
            first = _thread(
                lambda: TestClient(app).post("/api/v1/reports/daily/refresh")
            )
            assert slow_builder.entered.wait(3)
            res = TestClient(app).post("/api/v1/reports/daily/refresh")
            assert res.status_code == 202, res.text
            assert res.json() == {"status": "already_running"}
            assert slow_builder.calls == 1  # no second build queued
            slow_builder.release.set()
            first.join(5)
        finally:
            slow_builder.release.set()
            app.dependency_overrides.pop(get_settings, None)
        assert slow_builder.calls == 1 and slow_builder.max_active == 1

    def test_f3b_refresh_when_idle_still_starts(self, tmp_path, builder, client):
        _use(_settings(tmp_path))
        res = client.post("/api/v1/reports/daily/refresh")
        assert res.status_code == 202 and res.json() == {"status": "started"}
        # and a second refresh after the first finished is not blocked
        assert client.post("/api/v1/reports/daily/refresh").json() == {
            "status": "started"
        }
        assert builder.calls == 2

    def test_f3b_brief_job_during_rebuild_skips_or_waits_never_overlaps(
        self, tmp_path, slow_builder, monkeypatch
    ):
        monkeypatch.setattr("backend.app.scheduler.run_watchlist_sync", lambda s: None)
        from backend.app.reports import daily

        settings = _settings(tmp_path)
        first = _thread(daily.refresh_cached_brief, settings)
        assert slow_builder.entered.wait(3)
        job = _thread(_jobs(settings)["daily_brief"].func)
        time.sleep(0.3)
        assert slow_builder.max_active == 1
        slow_builder.release.set()
        first.join(5)
        job.join(5)
        assert not job.is_alive()
        assert slow_builder.max_active == 1  # one writer at a time
        assert json.loads(_cache(settings).read_text())["text"] == CANNED_TEXT

    def test_f3b_many_concurrent_refreshes_single_writer(self, tmp_path, slow_builder):
        from backend.app.reports import daily

        settings = _settings(tmp_path)
        threads = [_thread(daily.refresh_cached_brief, settings) for _ in range(4)]
        assert slow_builder.entered.wait(3)
        time.sleep(0.2)
        slow_builder.release.set()
        for t in threads:
            t.join(5)
        assert slow_builder.max_active == 1
        json.loads(_cache(settings).read_text())  # intact JSON


class TestF3bBriefAfterSync:
    def test_f3b_brief_job_syncs_first_when_not_done_today(self, tmp_path, sync_log):
        _jobs(_settings(tmp_path))["daily_brief"].func()
        assert sync_log == ["sync", "build"]

    def test_f3b_brief_job_skips_sync_when_todays_sync_done(self, tmp_path, sync_log):
        jobs = _jobs(_settings(tmp_path))
        jobs["data_sync"].func()
        jobs["daily_brief"].func()
        assert sync_log == ["sync", "build"]  # exactly one sync

    def test_f3b_brief_job_waits_for_running_sync(self, tmp_path, monkeypatch):
        from backend.app.reports import daily

        log: list[str] = []
        entered, release = threading.Event(), threading.Event()

        def slow_sync(settings):
            log.append("sync-start")
            entered.set()
            release.wait(5)
            log.append("sync-end")

        monkeypatch.setattr("backend.app.scheduler.run_watchlist_sync", slow_sync)
        monkeypatch.setattr(
            daily,
            "build_daily_brief",
            lambda s, *a, **k: log.append("build") or _canned_brief(),
        )
        jobs = _jobs(_settings(tmp_path))
        sync = _thread(jobs["data_sync"].func)
        assert entered.wait(3)
        brief = _thread(jobs["daily_brief"].func)
        time.sleep(0.3)
        assert "build" not in log  # still waiting for the sync
        release.set()
        sync.join(5)
        brief.join(5)
        assert log == ["sync-start", "sync-end", "build"]

    def test_f3b_brief_job_still_builds_if_sync_fails(
        self, tmp_path, builder, monkeypatch
    ):
        def boom(settings):
            raise RuntimeError("yfinance down")

        monkeypatch.setattr("backend.app.scheduler.run_watchlist_sync", boom)
        settings = _settings(tmp_path)
        _jobs(settings)["daily_brief"].func()
        assert builder.calls == 1 and _cache(settings).exists()


def _start(tmp_path, monkeypatch, now, cache_computed_at=None):
    from backend.app.data import universe
    from backend.app.scheduler import start_scheduler

    fake = _FakeScheduler()
    monkeypatch.setattr(universe, "_scheduler", fake)
    settings = _settings(tmp_path, scheduler_enabled=True)
    if cache_computed_at is not None:
        path = _cache(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {"text": CANNED_TEXT, "computed_at": cache_computed_at.isoformat()}
        path.write_text(json.dumps(body))
    start_scheduler(FastAPI(), settings, now=now)
    return fake


class TestF3bCatchUp:
    # Mon 23 Nov 2026 (GMT, UK == UTC).
    def test_f3b_catch_up_scheduled_weekday_after_8_no_brief_today(
        self, tmp_path, monkeypatch, sync_log
    ):
        fake = _start(tmp_path, monkeypatch, datetime(2026, 11, 23, 9, 30, tzinfo=UK))
        assert "brief_catch_up" in fake.funcs
        fake.funcs["brief_catch_up"]()
        assert sync_log == ["sync", "build"]

    def test_f3b_catch_up_when_cache_is_from_yesterday(
        self, tmp_path, monkeypatch, sync_log
    ):
        fake = _start(
            tmp_path,
            monkeypatch,
            datetime(2026, 11, 23, 9, 30, tzinfo=UK),
            cache_computed_at=datetime(2026, 11, 20, 8, 0, tzinfo=UK),
        )
        assert "brief_catch_up" in fake.funcs

    def test_f3b_no_catch_up_when_brief_exists_today(self, tmp_path, monkeypatch):
        fake = _start(
            tmp_path,
            monkeypatch,
            datetime(2026, 11, 23, 9, 30, tzinfo=UK),
            cache_computed_at=datetime(2026, 11, 23, 8, 1, tzinfo=UK),
        )
        assert "brief_catch_up" not in fake.funcs
        assert {"data_sync", "daily_brief"} <= set(fake.funcs)

    @pytest.mark.parametrize(
        "now",
        [
            datetime(2026, 11, 21, 10, 0, tzinfo=UK),  # Saturday
            datetime(2026, 11, 22, 10, 0, tzinfo=UK),  # Sunday
            datetime(2026, 11, 23, 7, 59, tzinfo=UK),  # Monday before 08:00
        ],
    )
    def test_f3b_no_catch_up_weekend_or_before_8(self, tmp_path, monkeypatch, now):
        fake = _start(tmp_path, monkeypatch, now)
        assert "brief_catch_up" not in fake.funcs

    def test_f3b_catch_up_uses_london_time_in_summer(self, tmp_path, monkeypatch):
        # 07:30 UTC on a BST day is 08:30 London: past 08:00, so catch up.
        now = datetime(2026, 7, 1, 7, 30, tzinfo=ZoneInfo("UTC"))
        fake = _start(tmp_path, monkeypatch, now)
        assert "brief_catch_up" in fake.funcs

    def test_f3b_disabled_scheduler_schedules_no_catch_up(self, tmp_path, monkeypatch):
        from backend.app.data import universe
        from backend.app.scheduler import start_scheduler

        fake = _FakeScheduler()
        monkeypatch.setattr(universe, "_scheduler", fake)
        start_scheduler(
            FastAPI(),
            _settings(tmp_path, scheduler_enabled=False),
            now=datetime(2026, 11, 23, 9, 30, tzinfo=UK),
        )
        assert fake.funcs == {}


class TestF3bInstallScriptRetry:
    def test_f3b_install_script_waits_between_bootout_and_bootstrap(self):
        text = (REPO / "scripts" / "install_launchd.sh").read_text()
        out = text.index("\nlaunchctl bootout")
        boot = text.index("\nlaunchctl bootstrap")
        assert out < boot
        between = text[out:boot]
        assert re.search(r"\bsleep\b", between), "no wait after bootout"
        # and bootstrap itself is retried, not a single bare attempt
        assert re.search(r"\b(for|while|until)\b", text[out:]), "no retry loop"
