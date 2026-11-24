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
