"""Momentum sleeve (6-1, top 10, equal weight, 2N hold buffer, no SPY filter)
in the Alpaca paper account, alongside the swing executor. No network: HTTP,
the market calendar and the price cache are all faked."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.app.api.execution as execution_module
from backend.app.config import Settings, get_settings
from backend.app.execution.alpaca_client import AlpacaExecutionClient
from backend.app.main import app

# ------------------------------------------ MARKET orders carry the order tag


class _RecordingSdk:
    def __init__(self):
        self.submitted = []

    def submit_order(self, order_data):
        self.submitted.append(order_data)
        return SimpleNamespace(
            id="o-1",
            symbol=order_data.symbol,
            qty=str(order_data.qty),
            side=SimpleNamespace(value="buy"),
            type=SimpleNamespace(value="market"),
            order_class=SimpleNamespace(value="simple"),
            status=SimpleNamespace(value="accepted"),
            submitted_at=None,
        )


def test_market_order_sends_client_order_id_as_a_day_order():
    sdk = _RecordingSdk()
    client = AlpacaExecutionClient("key", "secret", client=sdk)
    client.submit_market_order("aapl", 1.234567, "buy", client_order_id="mom-x")
    request = sdk.submitted[0]
    assert request.client_order_id == "mom-x"
    assert request.qty == 1.234567
    assert request.time_in_force.value == "day"


@pytest.fixture
def api(market):
    sdk = _RecordingSdk()
    fake = AlpacaExecutionClient("key", "secret", client=sdk)
    fake.get_positions = lambda: [{"symbol": "AAPL", "side": "long", "qty": 3.0}]
    app.dependency_overrides[execution_module._client] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: Settings()
    yield TestClient(app), sdk
    app.dependency_overrides.pop(execution_module._client, None)
    app.dependency_overrides.pop(get_settings, None)


def test_execution_route_passes_the_tag_on_market_orders(api):
    client, sdk = api
    body = {
        "ticker": "AAPL",
        "side": "buy",
        "qty": 0.5,
        "order_type": "MARKET",
        "intent": "ENTRY",
        "client_order_id": "mom-2026-12-AAPL",
    }
    assert client.post("/api/v1/execution/orders", json=body).status_code == 200
    assert sdk.submitted[0].client_order_id == "mom-2026-12-AAPL"


def test_market_entry_without_stop_still_runs_the_guards(api, market):
    """No guard is relaxed for the sleeve: a closed session still blocks."""
    client, sdk = api
    market.state = "CLOSED"
    body = {"ticker": "AAPL", "side": "buy", "qty": 0.5, "intent": "ENTRY"}
    assert client.post("/api/v1/execution/orders", json=body).status_code == 422
    assert sdk.submitted == []


def test_sleeve_exit_of_held_quantity_passes_a_closed_session(api, market):
    client, sdk = api
    market.state = "CLOSED"
    body = {"ticker": "AAPL", "side": "sell", "qty": 3.0, "intent": "EXIT"}
    assert client.post("/api/v1/execution/orders", json=body).status_code == 200
    # More than the account holds is not an exit: it is guarded (no short).
    body["qty"] = 3.5
    assert client.post("/api/v1/execution/orders", json=body).status_code == 422


# ------------------------------------------------------------------ settings


def test_sleeve_pct_defaults_to_30_percent_and_reads_the_env(monkeypatch):
    from pathlib import Path

    assert Settings().momentum_sleeve_pct == 0.30
    monkeypatch.setenv("MOMENTUM_SLEEVE_PCT", "0.25")
    get_settings.cache_clear()
    try:
        assert get_settings().momentum_sleeve_pct == 0.25
    finally:
        get_settings.cache_clear()
    # Its own state file, next to the swing executor's, under data/reports.
    assert Settings().momentum_state_path == Path("data/reports/momentum_sleeve.json")
    assert Settings(data_dir=Path("/x/raw")).momentum_state_path == Path(
        "/x/reports/momentum_sleeve.json"
    )


# ------------------------------------------------------- calendar and tagging

from datetime import date, datetime, time, timedelta  # noqa: E402

from backend.app.execution import momentum_sleeve as ms  # noqa: E402
from backend.app.execution.market_clock import NY  # noqa: E402

HOLIDAYS = {date(2026, 11, 26), date(2026, 12, 25), date(2027, 1, 1)}


def close_on(day: date):
    if day.weekday() >= 5 or day in HOLIDAYS:
        return None
    return datetime.combine(day, time(16), NY)


def test_tag_format():
    assert ms.order_tag("2026-12", "AAPL") == "mom-2026-12-AAPL"
    assert ms.order_tag("2027-01", "brk.b") == "mom-2027-01-BRK.B"


@pytest.mark.parametrize(
    "day, first",
    [
        (date(2026, 12, 1), True),  # Tuesday
        (date(2026, 12, 2), False),
        (date(2026, 11, 2), True),  # 1 Nov is a Sunday
        (date(2026, 11, 1), False),  # a Sunday is never a trading day
        (date(2027, 1, 4), True),  # 1 Jan holiday, then a weekend
        (date(2027, 1, 1), False),
        (date(2027, 1, 5), False),
    ],
)
def test_first_trading_day_detection(day, first):
    assert ms.is_first_trading_day(day, close_on) is first


def test_rank_day_is_the_last_session_of_the_previous_month():
    assert ms.rank_day_for(date(2026, 12, 1), close_on) == date(2026, 11, 30)
    assert ms.rank_day_for(date(2027, 1, 4), close_on) == date(2026, 12, 31)
    assert ms.rank_day_for(date(2026, 11, 2), close_on) == date(2026, 10, 30)


def test_next_rebalance_date():
    assert ms.next_rebalance(date(2026, 11, 24), None, close_on) == date(2026, 12, 1)
    # On the day itself, before the sleeve has traded this month: today.
    assert ms.next_rebalance(date(2026, 12, 1), None, close_on) == date(2026, 12, 1)
    done = {"month": "2026-12", "complete": True}
    assert ms.next_rebalance(date(2026, 12, 1), done, close_on) == date(2027, 1, 4)


# ------------------------------------------------------------------- ranking

import pandas as pd  # noqa: E402

from backend.app.quant import momentum  # noqa: E402

RANK_DAY = date(2026, 11, 30)


def _bars(index, close, volume=1_000_000):
    close = pd.Series(close, index=index, dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close,
            "Low": close,
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )


def universe(n=15, extra_after=0):
    """`n` steady uptrends T00 (slowest) to T(n-1) (fastest) to RANK_DAY,
    plus a penny stock and an illiquid name that would rank first."""
    index = pd.bdate_range(end=RANK_DAY, periods=200)
    if extra_after:
        index = index.append(pd.bdate_range(RANK_DAY + timedelta(1), periods=3))
    steps = pd.Series(range(len(index)), index=index, dtype=float)
    frames = {
        f"T{k:02d}": _bars(index, 50 + (k + 1) * 0.05 * steps) for k in range(n)
    }
    if extra_after:  # a late crash in the fastest name must not change ranks
        frames[f"T{n - 1:02d}"].iloc[-3:, :4] = 1.0
    frames["PENNY"] = _bars(index, 1 + 0.02 * steps, volume=100_000_000)
    frames["THIN"] = _bars(index, 50 + 5 * steps, volume=10)
    return frames, _bars(index, 400 + steps)


def test_holdings_on_reuses_the_backtest_selector():
    frames, spy = universe()
    panel = momentum.build_panel(frames, spy)
    i = panel.index.get_loc(pd.Timestamp(RANK_DAY))
    expected = list(momentum.momentum_selector(panel, ms.SLEEVE_CONFIG)(i, {"T02"}))
    got = momentum.holdings_on(panel, ms.SLEEVE_CONFIG, RANK_DAY, {"T02"})
    assert got == expected


def test_rank_targets_top_10_with_the_hold_buffer_on_the_rank_day_close():
    assert ms.SLEEVE_CONFIG.lookback == 126 and ms.SLEEVE_CONFIG.skip == 21
    assert ms.SLEEVE_CONFIG.top_n == 10 and ms.SLEEVE_CONFIG.buffer_mult == 2
    assert not ms.SLEEVE_CONFIG.market_filter
    frames, spy = universe(extra_after=3)
    targets, prices = ms.rank_targets(frames, spy, RANK_DAY, held=set())
    assert sorted(targets) == [f"T{k:02d}" for k in range(5, 15)]
    assert prices["T14"] == pytest.approx(frames["T14"].loc[str(RANK_DAY), "Close"])
    # A held name still ranked in the top 20 is kept over the 10th best.
    targets, _ = ms.rank_targets(frames, spy, RANK_DAY, held={"T02"})
    assert "T02" in targets and "T05" not in targets and len(targets) == 10


def test_rank_targets_fails_without_a_bar_on_the_rank_day():
    frames, spy = universe()
    with pytest.raises(ValueError, match="2026-12-01"):
        ms.rank_targets(frames, spy, date(2026, 12, 1), held=set())


# ---------------------------------------------------------------------- plan


def pos(symbol, qty, side="long"):
    return {"symbol": symbol, "qty": qty, "side": side}


def test_plan_buys_equal_weight_and_skips_names_held_by_swing():
    plan = ms.plan_rebalance(
        targets=["NEW", "SWG"],
        sleeve={},
        positions=[pos("SWG", 4)],
        prices={"NEW": 40.0, "SWG": 10.0},
        sleeve_value=1000.0,
        top_n=2,
    )
    assert [(o.ticker, o.side, o.qty) for o in plan.buys] == [("NEW", "buy", 12.5)]
    assert plan.sells == [] and plan.conflicts == ["SWG"]


def test_plan_exits_only_the_sleeve_quantity_and_never_more_than_held():
    plan = ms.plan_rebalance(
        targets=[],
        sleeve={"OLD": 5.0, "PART": 5.0, "GONE": 3.0},
        positions=[pos("OLD", 12.0), pos("PART", 2.0)],
        prices={},
        sleeve_value=1000.0,
        top_n=2,
    )
    assert [(o.ticker, o.side, o.qty) for o in plan.sells] == [
        ("OLD", "sell", 5.0),
        ("PART", "sell", 2.0),
    ]
    assert plan.buys == [] and plan.dropped == ["GONE"]


def test_plan_reweights_kept_names_and_skips_dust():
    plan = ms.plan_rebalance(
        targets=["KEEP", "SAME", "UP"],
        sleeve={"KEEP": 10.0, "SAME": 5.0, "UP": 1.0},
        positions=[pos("KEEP", 10.0), pos("SAME", 5.0), pos("UP", 1.0)],
        prices={"KEEP": 100.0, "SAME": 100.1, "UP": 100.0},
        sleeve_value=1500.0,
        top_n=3,
    )
    assert [(o.ticker, o.qty) for o in plan.sells] == [("KEEP", 5.0)]
    assert [(o.ticker, o.qty) for o in plan.buys] == [("UP", 4.0)]


def test_order_body_is_a_tagged_fractional_market_order():
    sell = ms.SleeveOrder("OLD", "sell", 1.5, 10.0)
    buy = ms.SleeveOrder("NEW", "buy", 0.123456, 40.0)
    assert sell.body("2026-12") == {
        "ticker": "OLD",
        "side": "sell",
        "qty": 1.5,
        "order_type": "MARKET",
        "intent": "EXIT",
        "client_order_id": "mom-2026-12-OLD",
    }
    assert buy.body("2026-12")["intent"] == "ENTRY"


# -------------------------------------------------------------------- runner

import importlib.util  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

UK = ZoneInfo("Europe/London")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "momentum_sleeve.py"
DEC_1 = datetime(2026, 12, 1, 14, 40, tzinfo=UK)  # 09:40 New York
TOP10 = [f"T{k:02d}" for k in range(5, 15)]


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {}

    def json(self):
        return self._body


class Session:
    """Fake backend: the brief, positions and account, and the order route
    (`status` per ticker, default 200)."""

    def __init__(self, positions=(), equity=10_000.0, brief=None, status=None):
        self.brief = brief or Resp(200, {"stale": False})
        self.positions = Resp(200, {"positions": list(positions)})
        self.account = Resp(200, {"equity": equity})
        self.status = status or {}
        self.posts: list[dict] = []
        self.gets: list[str] = []

    def get(self, url, timeout=None):
        self.gets.append(url)
        if url.endswith("/reports/daily"):
            return self.brief
        if url.endswith("/execution/positions"):
            return self.positions
        return self.account

    def post(self, url, json=None, timeout=None):
        assert url.endswith("/api/v1/execution/orders")
        self.posts.append(json)
        code = self.status.get(json["ticker"], 200)
        body = {"id": f"o-{json['ticker']}"}
        if code == 422:
            body = {"detail": "Order blocked (ENTRY): within 48h of earnings"}
        return Resp(code, body)


class Clock:
    def __init__(self, raises=None):
        self.raises = raises

    def close_on(self, day):
        if self.raises:
            raise self.raises
        return close_on(day)


@pytest.fixture
def sleeve(tmp_path):
    spec = importlib.util.spec_from_file_location("momentum_sleeve_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    settings = Settings(data_dir=tmp_path / "raw", allow_downloads=False)
    frames, spy = universe()

    def run(session, now=DEC_1, frames=frames, spy=spy, clock=None, **kw):
        return mod.run(
            session=session,
            now=now,
            settings=settings,
            clock=clock or Clock(),
            load=lambda s: (frames, spy),
            **kw,
        )

    mod.test_run = run
    mod.state_path = settings.momentum_state_path
    return mod


def state(mod) -> dict:
    return json.loads(mod.state_path.read_text())


def test_first_day_buys_ten_tagged_equal_weight_market_orders(sleeve):
    s = Session()
    out = sleeve.test_run(s)
    assert out["reason"] == "" and sorted(p["ticker"] for p in s.posts) == TOP10
    frames, _ = universe()
    for body in s.posts:
        t = body["ticker"]
        price = float(frames[t].loc[str(RANK_DAY), "Close"])
        assert body["order_type"] == "MARKET" and body["side"] == "buy"
        assert body["intent"] == "ENTRY"
        assert body["client_order_id"] == f"mom-2026-12-{t}"
        assert body["qty"] == pytest.approx(300.0 / price, abs=1e-6)
        assert body["qty"] * price <= 300.0
    saved = state(sleeve)
    assert saved["month"] == "2026-12" and saved["complete"] is True
    assert sorted(saved["holdings"]) == TOP10
    assert saved["start"]["date"] == "2026-12-01"
    assert saved["base_value"] == pytest.approx(3000.0, abs=0.01)


def test_rerun_in_the_same_month_places_nothing(sleeve):
    s = Session()
    sleeve.test_run(s)
    out = sleeve.test_run(s)
    assert len(s.posts) == 10 and "already rebalanced 2026-12" in out["reason"]


def test_rerun_after_a_blocked_buy_retries_only_that_name(sleeve):
    s = Session(status={"T14": 422})
    out = sleeve.test_run(s)
    assert "T14" in out["blocked"][0] and "earnings" in out["blocked"][0]
    assert state(sleeve)["complete"] is False
    assert "T14" not in state(sleeve)["holdings"]
    s.status = {}
    s.posts.clear()
    sleeve.test_run(s)
    assert [p["ticker"] for p in s.posts] == ["T14"]
    assert state(sleeve)["complete"] is True


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 12, 2, 14, 40, tzinfo=UK),  # second trading day
        datetime(2026, 11, 30, 14, 40, tzinfo=UK),
        datetime(2026, 12, 1, 14, 30, tzinfo=UK),  # 09:30 New York
    ],
)
def test_only_trades_after_0935_on_the_first_trading_day(sleeve, now):
    s = Session()
    out = sleeve.test_run(s, now=now)
    assert s.posts == [] and out["reason"]
    assert not sleeve.state_path.exists()


def test_target_held_by_the_swing_executor_is_skipped_and_logged(sleeve, caplog):
    s = Session(positions=[pos("T14", 3.0)])
    with caplog.at_level("INFO"):
        out = sleeve.test_run(s)
    assert "T14" not in [p["ticker"] for p in s.posts] and len(s.posts) == 9
    assert out["conflicts"] == ["T14"]
    assert "T14" in caplog.text and "swing" in caplog.text
    assert "T14" not in state(sleeve)["holdings"]


def test_exit_sells_only_the_sleeve_quantity_first(sleeve):
    sleeve.state_path.parent.mkdir(parents=True)
    sleeve.state_path.write_text(
        json.dumps({"month": "2026-11", "complete": True, "holdings": {"OLD": 2.0}})
    )
    s = Session(positions=[pos("OLD", 5.0)])
    sleeve.test_run(s)
    first = s.posts[0]
    assert first == {
        "ticker": "OLD",
        "side": "sell",
        "qty": 2.0,
        "order_type": "MARKET",
        "intent": "EXIT",
        "client_order_id": "mom-2026-12-OLD",
    }
    assert all(p["side"] == "buy" for p in s.posts[1:])
    assert "OLD" not in state(sleeve)["holdings"]


def test_a_failed_sell_fails_closed_before_any_buy(sleeve):
    sleeve.state_path.parent.mkdir(parents=True)
    sleeve.state_path.write_text(
        json.dumps({"month": "2026-11", "complete": True, "holdings": {"OLD": 2.0}})
    )
    s = Session(positions=[pos("OLD", 2.0)], status={"OLD": 422})
    out = sleeve.test_run(s)
    assert [p["ticker"] for p in s.posts] == ["OLD"]
    assert out["blocked"] and "buys skipped" in out["reason"]
    assert state(sleeve)["holdings"] == {"OLD": 2.0}


def _down(status, body=None):
    return Resp(status, body)


@pytest.mark.parametrize(
    "fault, words",
    [
        ({"brief": _down(404)}, "brief"),
        ({"brief": _down(200, {"stale": True})}, "stale"),
        ({"brief": _down(500)}, "brief"),
        ({"positions": _down(503, {"detail": "down"})}, "positions"),
        ({"positions": _down(200, {})}, "positions"),
        ({"account": _down(503)}, "account"),
        ({"account": _down(200, {"equity": 0})}, "account"),
    ],
)
def test_fails_closed_when_backend_data_is_unavailable(sleeve, fault, words):
    s = Session()
    for name, resp in fault.items():
        setattr(s, name, resp)
    out = sleeve.test_run(s)
    assert s.posts == [] and words in out["reason"]
    assert not sleeve.state_path.exists()


def test_fails_closed_without_the_calendar(sleeve):
    s = Session()
    out = sleeve.test_run(s, clock=Clock(raises=RuntimeError("no keys")))
    assert s.posts == [] and "calendar" in out["reason"]


def test_fails_closed_when_the_cache_lacks_the_rank_day(sleeve):
    frames, spy = universe()
    cut = {t: df.loc[:"2026-11-27"] for t, df in frames.items()}
    s = Session()
    out = sleeve.test_run(s, frames=cut, spy=spy.loc[:"2026-11-27"])
    assert s.posts == [] and "2026-11-30" in out["reason"]


def test_fails_closed_with_fewer_than_ten_eligible_names(sleeve):
    frames, spy = universe(n=6)
    s = Session()
    out = sleeve.test_run(s, frames=frames, spy=spy)
    assert s.posts == [] and "eligible" in out["reason"]


def test_fails_closed_on_an_unreadable_state_file(sleeve):
    sleeve.state_path.parent.mkdir(parents=True)
    sleeve.state_path.write_text("{not json")
    s = Session()
    out = sleeve.test_run(s)
    assert s.posts == [] and "state" in out["reason"]


def test_dry_run_plans_without_orders_or_state(sleeve):
    s = Session()
    out = sleeve.test_run(
        s, now=datetime(2026, 11, 24, 10, tzinfo=UK), dry_run=True, month="2026-12"
    )
    assert s.posts == [] and not sleeve.state_path.exists()
    assert out["rank_day"] == "2026-11-30" and out["month"] == "2026-12"
    assert sorted(o.ticker for o in out["plan"].buys) == TOP10


def test_sleeve_size_follows_the_setting(sleeve, tmp_path):
    from dataclasses import replace

    s = Session()
    settings = replace(
        Settings(data_dir=tmp_path / "raw", allow_downloads=False),
        momentum_sleeve_pct=0.5,
    )
    frames, spy = universe()
    sleeve.run(
        session=s,
        now=DEC_1,
        settings=settings,
        clock=Clock(),
        load=lambda _: (frames, spy),
    )
    notional = sum(
        p["qty"] * float(frames[p["ticker"]].loc[str(RANK_DAY), "Close"])
        for p in s.posts
    )
    assert notional == pytest.approx(5000.0, abs=0.1)
