"""Batch 02 F5: forward-test journal (Alpaca fills) and the weekly report.

Agreed interface (Gate 1 + tech-expert):
- `broker_fills` table created idempotently by `db.session._create_tables`.
- `AlpacaExecutionClient.get_fill_activities(after)` returns Alpaca's raw FILL
  activity dicts (`id`, `order_id`, `symbol`, `side`, `qty`, `price`,
  `transaction_time`, ...) via `client.get("/account/activities/FILL", ...)`
  with `page_token` pagination.
- `journal.fills.ingest_fills(settings, client, now=None) -> int` and
  `round_trips(fills_df) -> DataFrame` (TradeJournal columns + `qty`).
- `reports.weekly.build_weekly_report(settings, now=None) -> WeeklyReport`.
- Jobs `weekly_report` (Sat 09:00 UK) and `fills_ingest` (weekdays 21:30 UK);
  `GET /api/v1/reports/weekly`, `POST /api/v1/reports/weekly/send`.

Seams: a fake Alpaca SDK client (never the network), a throwaway DuckDB via
`SCANS_DB_PATH`, fake `requests.post` for Telegram, `latest_gbp_rate` patched.
Net costs follow `api/order_ticket.py`: fx_cost_pct x conversions x entry
notional per round trip.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.data.fx import FxRate
from backend.app.db import session as db_session
from backend.app.execution import alpaca_client as alpaca_module
from backend.app.execution.alpaca_client import (
    AlpacaExecutionClient,
    AlpacaNotConfiguredError,
)
from backend.app.journal.executor import TradeJournal
from backend.app.main import app

UK = ZoneInfo("Europe/London")
SAT_0900 = datetime(2026, 11, 21, 9, 0, tzinfo=UK)  # week = Mon 16 Nov onwards
TELEGRAM_LIMIT = 4096
MIN_GRACE = 6 * 3600
FILLS_COLUMNS = {
    "activity_id",
    "order_id",
    "parent_order_id",
    "leg",
    "symbol",
    "side",
    "qty",
    "price",
    "filled_at",
    "ingested_at",
}


# --------------------------------------------------------------- fixtures


def _act(aid, order_id, symbol, side, qty, price, when, kind="fill"):
    """One raw Alpaca FILL account activity, shaped as the REST API sends it."""
    return {
        "id": aid,
        "activity_type": "FILL",
        "transaction_time": when,
        "type": kind,
        "price": str(price),
        "qty": str(qty),
        "side": side,
        "symbol": symbol,
        "leaves_qty": "0",
        "order_id": order_id,
        "cum_qty": str(qty),
        "order_status": "filled" if kind == "fill" else "partially_filled",
    }


#: AAA: entry fills in 2 parts, take-profit leg fills in 2 parts, the stop
#: leg is cancelled (no fills). qty 10, avg entry 100.7, avg exit 110.3,
#: pnl +96.  CCC: stop leg hit Friday, pnl -10.  DDD: last week, pnl +20.
#: BBB: still open.  Cumulative n=3, 2 wins, gross +106; this week 2.
STANDARD = [
    _act("a1", "o-aaa", "AAA", "buy", 3, 100.0, "2026-11-16T14:30:00Z", "partial_fill"),
    _act("a2", "o-aaa", "AAA", "buy", 7, 101.0, "2026-11-16T14:30:05Z"),
    _act(
        "a3",
        "o-aaa-tp",
        "AAA",
        "sell",
        4,
        110.0,
        "2026-11-18T15:00:00Z",
        "partial_fill",
    ),
    _act("a4", "o-aaa-tp", "AAA", "sell", 6, 110.5, "2026-11-18T15:00:02Z"),
    _act("c1", "o-ccc", "CCC", "buy", 5, 50.0, "2026-11-17T15:00:00Z"),
    _act("c2", "o-ccc-sl", "CCC", "sell", 5, 48.0, "2026-11-20T20:00:00Z"),
    _act("d1", "o-ddd", "DDD", "buy", 2, 200.0, "2026-11-10T15:00:00Z"),
    _act("d2", "o-ddd-tp", "DDD", "sell", 2, 210.0, "2026-11-13T15:00:00Z"),
    _act("b1", "o-bbb", "BBB", "buy", 4, 30.0, "2026-11-19T15:00:00Z"),
]


class FakeFillClient:
    """Stands in for `AlpacaExecutionClient` in `ingest_fills`."""

    def __init__(self, activities=(), error: Exception | None = None):
        self.activities = list(activities)
        self.error = error
        self.calls = []

    def get_fill_activities(self, after=None):
        self.calls.append(after)
        if self.error:
            raise self.error
        return list(self.activities)


class FakeSdk:
    """Stands in for alpaca-py's `TradingClient`: only the raw REST `get`.
    Honours `page_size` (default 100) and `page_token` (the last id seen)."""

    instances: list["FakeSdk"] = []
    activities: list[dict] = []
    error: Exception | None = None

    def __init__(self, *args, **kwargs):
        self.calls = []
        FakeSdk.instances.append(self)

    def get(self, path, data=None, **kwargs):
        params = dict(data or kwargs.get("params") or {})
        self.calls.append((path, params))
        if FakeSdk.error is not None:
            raise FakeSdk.error
        size = int(params.get("page_size") or 100)
        start = 0
        token = params.get("page_token")
        if token:
            ids = [a["id"] for a in FakeSdk.activities]
            start = ids.index(token) + 1
        return FakeSdk.activities[start : start + size]  # noqa: E203


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch):
    """Jobs and endpoints build the report for "now"; pin it to SAT_0900 so
    the expected week doesn't depend on the day the suite runs."""
    from backend.app.reports import weekly

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return SAT_0900.astimezone(tz) if tz else SAT_0900.replace(tzinfo=None)

    monkeypatch.setattr(weekly, "datetime", _Frozen)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANS_DB_PATH", str(tmp_path / "f5.duckdb"))
    db_session.reset_for_tests()
    yield
    db_session.reset_for_tests()


@pytest.fixture
def fx(monkeypatch):
    """GBP/USD fixed at 1.25 wherever the report reads it."""
    from backend.app.data import fx as fx_module

    rate = FxRate(1.25, datetime(2026, 11, 21, 8, 59), "live")
    monkeypatch.setattr(fx_module, "latest_gbp_rate", lambda *a, **k: rate)
    try:
        from backend.app.reports import weekly
    except ImportError:
        return rate
    monkeypatch.setattr(weekly, "latest_gbp_rate", lambda *a, **k: rate, raising=False)
    return rate


@pytest.fixture
def sdk(monkeypatch):
    """Every lazily built Alpaca `TradingClient` is a `FakeSdk`."""
    FakeSdk.instances, FakeSdk.activities, FakeSdk.error = [], list(STANDARD), None
    monkeypatch.setattr(alpaca_module, "TradingClient", FakeSdk)
    for name in ("journal.fills", "reports.weekly", "scheduler", "api.reports"):
        try:
            module = __import__(f"backend.app.{name}", fromlist=["x"])
        except ImportError:
            continue
        monkeypatch.setattr(module, "TradingClient", FakeSdk, raising=False)
    return FakeSdk


def _settings(tmp_path, status=None, **kw) -> Settings:
    data = tmp_path / "raw"
    data.mkdir(exist_ok=True)
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"symbols": ["AAA", "BBB"], "names": {}}))
    if status is not None:
        (tmp_path / "universe_status.json").write_text(json.dumps(status))
    kw.setdefault("scheduler_enabled", False)
    return Settings(
        data_dir=data,
        allow_downloads=False,
        universe_path=universe,
        journal_path=tmp_path / "trades.csv",
        alert_config_path=tmp_path / "alerts_channels.json",
        **kw,
    )


def _status(added=("NEW1", "NEW2"), removed=("OLD1",), skipped=1, kept=False):
    return {
        "last_attempt": "2026-11-01T07:00:00+00:00",
        "kept_previous": kept,
        "reason": "sp500 source returned 12 symbols" if kept else None,
        "source_status": {"sp500": "live", "nasdaq100": "live"},
        "last_changes": {
            "added": list(added),
            "removed": list(removed),
            "renamed": [],
            "skipped": [
                {"symbol": f"SK{i}", "reason": "history fetch failed: no data"}
                for i in range(skipped)
            ],
        },
    }


def _fills_df(rows) -> pd.DataFrame:
    """`broker_fills`-shaped frame from (id, order, parent, leg, sym, side,
    qty, price, ISO time) tuples."""
    cols = ["activity_id", "order_id", "parent_order_id", "leg", "symbol"]
    cols += ["side", "qty", "price", "filled_at"]
    df = pd.DataFrame(rows, columns=cols)
    df["filled_at"] = pd.to_datetime(df["filled_at"], utc=True, format="ISO8601")
    df["ingested_at"] = pd.Timestamp("2026-11-21T09:00:00Z")
    return df


def _ingest(settings, activities=STANDARD):
    from backend.app.journal.fills import ingest_fills

    return ingest_fills(settings, FakeFillClient(activities), now=SAT_0900)


def _line(text: str, *keys: str) -> str:
    for line in text.splitlines():
        if any(k.lower() in line.lower() for k in keys):
            return line
    raise AssertionError(f"no line with {keys!r} in:\n{text}")


def _report(settings):
    from backend.app.reports.weekly import build_weekly_report

    return build_weekly_report(settings, now=SAT_0900)


# --------------------------------------------------------------- migration


class TestF5Migration:
    def test_f5_broker_fills_table_has_agreed_columns(self, tmp_path):
        conn = duckdb.connect(str(tmp_path / "m.duckdb"))
        db_session._create_tables(conn)
        cols = {r[0] for r in conn.execute("DESCRIBE broker_fills").fetchall()}
        assert cols == FILLS_COLUMNS

    def test_f5_create_tables_is_idempotent_and_keeps_rows(self, tmp_path):
        conn = duckdb.connect(str(tmp_path / "m.duckdb"))
        db_session._create_tables(conn)
        conn.execute(
            "INSERT INTO broker_fills (activity_id, symbol, side, qty, price) "
            "VALUES ('x1', 'AAA', 'buy', 1, 10)"
        )
        db_session._create_tables(conn)
        assert conn.execute("SELECT count(*) FROM broker_fills").fetchone()[0] == 1

    def test_f5_activity_id_is_primary_key(self, tmp_path):
        conn = duckdb.connect(str(tmp_path / "m.duckdb"))
        db_session._create_tables(conn)
        insert = (
            "INSERT INTO broker_fills (activity_id, symbol, side, qty, price) "
            "VALUES ('x1', 'AAA', 'buy', 1, 10)"
        )
        conn.execute(insert)
        with pytest.raises(duckdb.ConstraintException):
            conn.execute(insert)

    def test_f5_rollback_drop_table_then_recreate(self, tmp_path):
        conn = duckdb.connect(str(tmp_path / "m.duckdb"))
        db_session._create_tables(conn)
        conn.execute("DROP TABLE broker_fills")
        db_session._create_tables(conn)
        assert conn.execute("SELECT count(*) FROM broker_fills").fetchone()[0] == 0


# --------------------------------------------------------------- Alpaca


def _paged(n):
    return [
        _act(f"id{i:04d}", f"o{i}", "AAA", "buy", 1, 10.0, "2026-11-16T15:00:00Z")
        for i in range(n)
    ]


class TestF5AlpacaActivities:
    def test_f5_get_fill_activities_follows_page_token(self):
        FakeSdk.activities, FakeSdk.error = _paged(250), None
        fake = FakeSdk()
        got = AlpacaExecutionClient("k", "s", client=fake).get_fill_activities(None)
        assert [a["id"] for a in got] == [a["id"] for a in _paged(250)]
        assert len(fake.calls) >= 3
        assert all(path == "/account/activities/FILL" for path, _ in fake.calls)
        assert any(p.get("page_token") for _, p in fake.calls[1:])

    def test_f5_get_fill_activities_passes_after(self):
        FakeSdk.activities, FakeSdk.error = _paged(2), None
        fake = FakeSdk()
        after = datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)
        AlpacaExecutionClient("k", "s", client=fake).get_fill_activities(after)
        assert str(fake.calls[0][1].get("after", "")).startswith("2026-11-16")

    def test_f5_get_fill_activities_times_out_on_a_hung_call(self, monkeypatch):
        import threading

        from backend.app.execution import alpaca_client as ac

        release = threading.Event()

        class Hung:
            def get(self, path, data=None, **kwargs):
                release.wait(5)
                return []

        monkeypatch.setattr(ac, "ACTIVITIES_TIMEOUT_SECONDS", 0.2)
        try:
            with pytest.raises(TimeoutError):
                AlpacaExecutionClient("k", "s", client=Hung()).get_fill_activities()
        finally:
            release.set()

    def test_f5_get_fill_activities_stops_when_page_token_is_ignored(self):
        full_page = _paged(100)

        class Repeats:
            calls = 0

            def get(self, path, data=None, **kwargs):
                Repeats.calls += 1
                return list(full_page)

        got = AlpacaExecutionClient("k", "s", client=Repeats()).get_fill_activities()
        assert Repeats.calls == 2  # second page repeats the last id -> stop
        assert len(got) == 200

    def test_f5_get_fill_activities_empty_account(self):
        FakeSdk.activities, FakeSdk.error = [], None
        client = AlpacaExecutionClient("k", "s", client=FakeSdk())
        assert client.get_fill_activities(None) == []

    def test_f5_get_fill_activities_requires_keys(self):
        with pytest.raises(AlpacaNotConfiguredError):
            AlpacaExecutionClient(None, None).get_fill_activities(None)

    def test_f5_get_fill_activities_raises_on_5xx(self):
        FakeSdk.activities = _paged(2)
        FakeSdk.error = requests.HTTPError("503 Service Unavailable")
        try:
            with pytest.raises(requests.HTTPError):
                AlpacaExecutionClient("k", "s", client=FakeSdk()).get_fill_activities(
                    None
                )
        finally:
            FakeSdk.error = None


# --------------------------------------------------------------- ingest


class TestF5Ingest:
    def test_f5_ingest_inserts_every_new_fill(self, tmp_path, db):
        assert _ingest(_settings(tmp_path)) == len(STANDARD)
        with db_session.get_connection() as conn:
            row = conn.execute(
                "SELECT order_id, symbol, side, qty, price, filled_at "
                "FROM broker_fills WHERE activity_id = 'a2'"
            ).fetchone()
        assert row[:5] == ("o-aaa", "AAA", "buy", 7.0, 101.0)
        filled = pd.Timestamp(row[5])
        if filled.tzinfo is not None:
            filled = filled.tz_convert("UTC").tz_localize(None)
        assert filled == pd.Timestamp("2026-11-16 14:30:05")  # stored as UTC

    def test_f5_reingest_adds_zero_rows(self, tmp_path, db):
        settings = _settings(tmp_path)
        _ingest(settings)
        assert _ingest(settings) == 0
        with db_session.get_connection() as conn:
            n = conn.execute("SELECT count(*) FROM broker_fills").fetchone()[0]
        assert n == len(STANDARD)

    def test_f5_overlapping_batch_adds_only_new(self, tmp_path, db):
        settings = _settings(tmp_path)
        _ingest(settings, STANDARD[:5])
        assert _ingest(settings, STANDARD[3:]) == len(STANDARD) - 5

    def test_f5_ingest_nothing_new(self, tmp_path, db):
        assert _ingest(_settings(tmp_path), []) == 0

    def test_f5_ingest_alpaca_down_raises_and_writes_nothing(self, tmp_path, db):
        from backend.app.journal.fills import ingest_fills

        down = FakeFillClient(error=requests.ConnectionError("alpaca down"))
        with pytest.raises(requests.ConnectionError):
            ingest_fills(_settings(tmp_path), down, now=SAT_0900)
        with db_session.get_connection() as conn:
            assert conn.execute("SELECT count(*) FROM broker_fills").fetchone()[0] == 0


# --------------------------------------------------------------- round trips


def _round_trips(rows):
    from backend.app.journal.fills import round_trips

    return round_trips(_fills_df(rows))


BRACKET = [
    ("a1", "o-aaa", None, "entry", "AAA", "buy", 3, 100.0, "2026-11-16T14:30:00Z"),
    ("a2", "o-aaa", None, "entry", "AAA", "buy", 7, 101.0, "2026-11-16T14:30:05Z"),
    (
        "a3",
        "o-tp",
        "o-aaa",
        "take_profit",
        "AAA",
        "sell",
        4,
        110.0,
        "2026-11-18T15:00Z",
    ),
    (
        "a4",
        "o-tp",
        "o-aaa",
        "take_profit",
        "AAA",
        "sell",
        6,
        110.5,
        "2026-11-18T15:01Z",
    ),
]


class TestF5RoundTrips:
    def test_f5_partial_fills_and_bracket_legs_make_one_trade(self):
        trades = _round_trips(BRACKET)
        assert len(trades) == 1
        t = trades.iloc[0]
        assert t["ticker"] == "AAA"
        assert t["qty"] == pytest.approx(10)
        assert t["entry_price"] == pytest.approx(100.7)
        assert t["exit_price"] == pytest.approx(110.3)
        assert t["pnl"] == pytest.approx(96.0)

    def test_f5_entry_and_exit_dates_from_first_and_last_fill(self):
        t = _round_trips(BRACKET).iloc[0]
        assert pd.Timestamp(t["entry_date"]).date() == date(2026, 11, 16)
        assert pd.Timestamp(t["exit_date"]).date() == date(2026, 11, 18)

    def test_f5_has_trade_journal_columns(self, tmp_path):
        journal_cols = set(TradeJournal(str(tmp_path / "j.csv")).df.columns)
        trades = _round_trips(BRACKET)
        assert journal_cols | {"qty"} <= set(trades.columns)

    def test_f5_open_position_is_not_a_trade(self):
        rows = [
            ("b1", "o-b", None, "entry", "BBB", "buy", 4, 30.0, "2026-11-19T15:00Z")
        ]
        assert len(_round_trips(rows)) == 0

    def test_f5_partly_closed_position_is_not_a_trade(self):
        rows = [
            ("b1", "o-b", None, "entry", "BBB", "buy", 4, 30.0, "2026-11-19T15:00Z"),
            (
                "b2",
                "o-bx",
                "o-b",
                "stop_loss",
                "BBB",
                "sell",
                1,
                29.0,
                "2026-11-20T15:00Z",
            ),
        ]
        assert len(_round_trips(rows)) == 0

    def test_f5_two_trades_same_ticker_stay_separate(self):
        rows = [
            ("1", "e1", None, None, "AAA", "buy", 2, 10.0, "2026-11-02T15:00Z"),
            ("2", "x1", None, None, "AAA", "sell", 2, 12.0, "2026-11-03T15:00Z"),
            ("3", "e2", None, None, "AAA", "buy", 5, 20.0, "2026-11-09T15:00Z"),
            ("4", "x2", None, None, "AAA", "sell", 5, 19.0, "2026-11-10T15:00Z"),
        ]
        trades = _round_trips(rows).sort_values("entry_date")
        assert list(trades["pnl"].round(6)) == [4.0, -5.0]
        assert list(trades["qty"]) == [2, 5]

    def test_f5_unordered_input_is_sorted_by_fill_time(self):
        trades = _round_trips(list(reversed(BRACKET)))
        assert len(trades) == 1
        assert trades.iloc[0]["pnl"] == pytest.approx(96.0)

    def test_f5_empty_fills_give_empty_trades_with_columns(self):
        trades = _round_trips([])
        assert len(trades) == 0
        assert {"ticker", "entry_price", "exit_price", "pnl", "qty"} <= set(
            trades.columns
        )


# --------------------------------------------------------------- the report


class TestF5WeeklyReport:
    def test_f5_this_week_trade_count(self, tmp_path, db, fx):
        settings = _settings(tmp_path, status=_status())
        _ingest(settings)
        line = _line(_report(settings).text, "this week")
        assert re.search(r"\b2\b", line) and not re.search(r"\b3\b", line), line

    def test_f5_cumulative_record_usd_gbp_win_rate(self, tmp_path, db, fx):
        settings = _settings(tmp_path, status=_status())
        _ingest(settings)
        text = _report(settings).text
        line = _line(text, "since start", "cumulative")
        assert re.search(r"\b3\b", line), line
        assert re.search(r"6[67](\.\d)?%", text), text
        assert re.search(r"\$\s?106(\.00)?\b", text), text
        assert re.search(r"£\s?84\.80", text), text  # 106 / 1.25
        assert re.search(r"(avg|average) R", text, re.I), text

    def test_f5_net_is_after_fx_costs(self, tmp_path, db, fx):
        settings = _settings(
            tmp_path,
            status=_status(),
            fx_cost_pct=0.0015,
            fx_conversions_per_round_trip=2,
        )
        _ingest(settings)
        text = _report(settings).text
        # 0.3% of entry notional (1007 + 250 + 400) = 4.971 -> 101.03
        assert re.search(r"\$\s?101\.03", text), text
        assert not re.search(r"\$\s?106(\.00)?\b", text), text

    def test_f5_no_spy_comparison_below_30_trades(self, tmp_path, db, fx):
        settings = _settings(tmp_path, status=_status())
        _ingest(settings)
        text = _report(settings).text
        assert "n too small (3/30)" in text, text
        for line in text.splitlines():
            if "SPY" in line:
                assert "%" not in line, line

    def test_f5_spy_comparison_at_30_trades(self, tmp_path, db, fx):
        settings = _settings(tmp_path, status=_status())
        index = pd.bdate_range(end="2026-11-20", periods=300)
        close = pd.Series([400 + i * 0.5 for i in range(len(index))], index=index)
        pd.DataFrame(
            {
                "Open": close,
                "High": close + 1,
                "Low": close - 1,
                "Close": close,
                "Volume": [1_000_000] * len(index),
            },
            index=index,
        ).to_parquet(settings.data_dir / "SPY.parquet")
        acts = []
        start = datetime(2026, 8, 3, 15, tzinfo=timezone.utc)
        for i in range(30):
            t = start + timedelta(days=3 * i)
            acts.append(_act(f"e{i}", f"oe{i}", "EEE", "buy", 1, 100.0, t.isoformat()))
            acts.append(
                _act(
                    f"x{i}",
                    f"ox{i}",
                    "EEE",
                    "sell",
                    1,
                    101.0,
                    (t + timedelta(days=1)).isoformat(),
                )
            )
        _ingest(settings, acts)
        text = _report(settings).text
        assert "too small" not in text, text
        assert "%" in _line(text, "SPY"), text

    def test_f5_universe_line_added_removed_skipped(self, tmp_path, db, fx):
        settings = _settings(tmp_path, status=_status(skipped=3))
        _ingest(settings)
        text = _report(settings).text
        line = _line(text, "universe")
        assert re.search(r"\+2\s*[−-]1", line), line
        assert re.search(r"\b3\b", _line(text, "skipped")), text
        assert "kept previous" not in text.lower()

    def test_f5_universe_kept_previous_shows_reason(self, tmp_path, db, fx):
        settings = _settings(
            tmp_path, status=_status(added=(), removed=(), skipped=0, kept=True)
        )
        _ingest(settings)
        text = _report(settings).text
        assert "kept previous list: sp500 source returned 12 symbols" in text, text

    def test_f5_missing_universe_status_still_builds(self, tmp_path, db, fx):
        settings = _settings(tmp_path)
        _ingest(settings)
        assert "Research only, paper trading" in _report(settings).text

    def test_f5_open_positions_count(self, tmp_path, db, fx):
        settings = _settings(tmp_path, status=_status())
        _ingest(settings)
        line = _line(_report(settings).text, "open")
        assert re.search(r"\b1\b", line), line

    def test_f5_footer_last_line(self, tmp_path, db, fx):
        settings = _settings(tmp_path, status=_status())
        _ingest(settings)
        last = [ln for ln in _report(settings).text.splitlines() if ln.strip()][-1]
        assert "Research only, paper trading" in last

    def test_f5_zero_trades_says_so(self, tmp_path, db, fx):
        settings = _settings(tmp_path, status=_status())
        text = _report(settings).text
        assert re.search(r"no (closed )?trades", text, re.I), text
        assert "nan" not in text.lower()
        assert "n too small (0/30)" in text, text

    def test_f5_one_message_under_telegram_limit_when_large(self, tmp_path, db, fx):
        from backend.app.alerts.dispatcher import split_message

        big = _status(
            added=[f"ADD{i:03d}" for i in range(500)],
            removed=[f"REM{i:03d}" for i in range(500)],
            skipped=200,
        )
        settings = _settings(tmp_path, status=big)
        acts = []
        for i in range(80):
            sym = f"T{i:02d}"
            acts.append(
                _act(f"e{i}", f"oe{i}", sym, "buy", 1, 10.0, "2026-11-16T15:00:00Z")
            )
            acts.append(
                _act(f"x{i}", f"ox{i}", sym, "sell", 1, 11.0, "2026-11-19T15:00:00Z")
            )
        _ingest(settings, acts)
        text = _report(settings).text
        assert len(text) < TELEGRAM_LIMIT
        assert len(split_message(text)) == 1
        assert re.search(r"\+500\s*[−-]500", text), text

    def test_f5_plain_text_no_markdown(self, tmp_path, db, fx):
        settings = _settings(tmp_path, status=_status())
        _ingest(settings)
        text = _report(settings).text
        assert "*" not in text and "`" not in text and "<b>" not in text


# --------------------------------------------------------------- scheduler


def _jobs(settings):
    from backend.app.scheduler import build_jobs

    return {job.id: job for job in build_jobs(settings)}


class _Resp:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Bad Request")


@pytest.fixture
def telegram(monkeypatch):
    sent = []

    def post(url, json=None, timeout=None, **kw):
        sent.append(json["text"])
        return _Resp(200)

    monkeypatch.setattr(requests, "post", post)
    return sent


def _live_settings(tmp_path, **kw):
    return _settings(
        tmp_path,
        status=_status(),
        alpaca_api_key="key",
        alpaca_api_secret="secret",
        telegram_bot_token="123:SECRETTOKEN",
        telegram_chat_id="42",
        **kw,
    )


class TestF5Jobs:
    def test_f5_weekly_report_saturday_0900_uk(self, tmp_path):
        trigger = _jobs(_settings(tmp_path))["weekly_report"].trigger
        assert str(trigger.timezone) == "Europe/London"
        fire = trigger.get_next_fire_time(None, datetime(2026, 11, 18, 12, tzinfo=UK))
        assert fire.astimezone(UK).replace(tzinfo=None) == datetime(2026, 11, 21, 9)
        nxt = trigger.get_next_fire_time(None, fire + timedelta(minutes=1))
        assert nxt.astimezone(UK).replace(tzinfo=None) == datetime(2026, 11, 28, 9)

    def test_f5_weekly_report_holds_0900_across_uk_dst(self, tmp_path):
        trigger = _jobs(_settings(tmp_path))["weekly_report"].trigger
        # UK clocks go forward Sun 29 Mar 2026.
        before = trigger.get_next_fire_time(None, datetime(2026, 3, 27, 12, tzinfo=UK))
        after = trigger.get_next_fire_time(None, before + timedelta(minutes=1))
        assert before.astimezone(UK).replace(tzinfo=None) == datetime(2026, 3, 28, 9)
        assert after.astimezone(UK).replace(tzinfo=None) == datetime(2026, 4, 4, 9)
        assert after.astimezone(timezone.utc).hour == 8

    def test_f5_fills_ingest_weekdays_2130_uk(self, tmp_path):
        trigger = _jobs(_settings(tmp_path))["fills_ingest"].trigger
        assert str(trigger.timezone) == "Europe/London"
        fires, prev = [], datetime(2026, 11, 20, 22, tzinfo=UK)
        for _ in range(6):
            prev = trigger.get_next_fire_time(None, prev + timedelta(minutes=1))
            fires.append(prev.astimezone(UK))
        assert [f.weekday() for f in fires] == [0, 1, 2, 3, 4, 0]
        assert all((f.hour, f.minute) == (21, 30) for f in fires)

    @pytest.mark.parametrize("job_id", ["weekly_report", "fills_ingest"])
    def test_f5_missed_job_runs_once_after_wake(self, tmp_path, job_id):
        job = _jobs(_settings(tmp_path))[job_id]
        assert job.coalesce is True
        assert job.misfire_grace_time >= MIN_GRACE

    def test_f5_fills_ingest_job_writes_fills(self, tmp_path, db, sdk):
        _jobs(_live_settings(tmp_path))["fills_ingest"].func()
        with db_session.get_connection() as conn:
            n = conn.execute("SELECT count(*) FROM broker_fills").fetchone()[0]
        assert n == len(STANDARD)

    def test_f5_weekly_job_ingests_then_sends_one_message(
        self, tmp_path, db, sdk, fx, telegram
    ):
        _jobs(_live_settings(tmp_path))["weekly_report"].func()
        assert len(telegram) == 1
        assert "journal not updated" not in telegram[0].lower()
        assert re.search(r"\b2\b", _line(telegram[0], "this week"))

    def test_f5_weekly_job_alpaca_down_still_sends(
        self, tmp_path, db, sdk, fx, telegram
    ):
        sdk.error = requests.ConnectionError("alpaca down")
        _jobs(_live_settings(tmp_path))["weekly_report"].func()
        assert len(telegram) == 1
        assert "journal not updated" in telegram[0].lower()
        assert "Research only, paper trading" in telegram[0]

    def test_f5_weekly_job_survives_telegram_400(
        self, tmp_path, db, sdk, fx, monkeypatch
    ):
        monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(400))
        _jobs(_live_settings(tmp_path))["weekly_report"].func()  # must not raise


# --------------------------------------------------------------- endpoints


@pytest.fixture
def api(tmp_path, db, sdk, fx):
    settings = _live_settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app), settings
    app.dependency_overrides.pop(get_settings, None)


class TestF5Api:
    def test_f5_get_weekly_builds_from_db_without_alpaca(self, api, sdk, monkeypatch):
        client, settings = api
        _ingest(settings)

        def no_post(*a, **k):
            raise AssertionError("GET must not send")

        monkeypatch.setattr(requests, "post", no_post)
        response = client.get("/api/v1/reports/weekly")
        assert response.status_code == 200
        assert re.search(r"\b2\b", _line(response.json()["text"], "this week"))
        assert all(not inst.calls for inst in sdk.instances)

    def test_f5_post_send_ingests_and_sends(self, api, telegram):
        client, _ = api
        response = client.post("/api/v1/reports/weekly/send")
        assert response.status_code == 200
        assert response.json()["sent"] is True
        assert len(telegram) == 1
        assert re.search(r"\b2\b", _line(telegram[0], "this week"))

    def test_f5_post_send_alpaca_down_still_sends(self, api, sdk, telegram):
        sdk.error = requests.HTTPError("500 Internal Server Error")
        client, _ = api
        response = client.post("/api/v1/reports/weekly/send")
        assert response.status_code == 200
        assert response.json()["sent"] is True
        assert "journal not updated" in telegram[0].lower()

    def test_f5_post_send_telegram_400_scrubs_token(self, api, monkeypatch):
        client, _ = api

        def bad(url, **kw):
            raise requests.HTTPError(f"400 Client Error for url: {url}")

        monkeypatch.setattr(requests, "post", bad)
        response = client.post("/api/v1/reports/weekly/send")
        assert response.status_code == 200
        body = response.json()
        assert body["sent"] is False
        assert "SECRETTOKEN" not in json.dumps(body)

    def test_f5_post_send_without_telegram_is_503(self, tmp_path, db, sdk, fx):
        settings = _settings(tmp_path, status=_status())
        app.dependency_overrides[get_settings] = lambda: settings
        try:
            response = TestClient(app).post("/api/v1/reports/weekly/send")
        finally:
            app.dependency_overrides.pop(get_settings, None)
        assert response.status_code == 503
