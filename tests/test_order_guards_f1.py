"""F1 (batch 02) - guarded orders.

`enforce_order_guards` unit behaviour plus its wiring into both order routes
(`/api/v1/execution/orders`, `/api/v1/orders/submit`). The clock is the
`FakeMarketClock` from `conftest.py`; earnings/split fetchers are
monkeypatched on `execution.guards`; no broker or network is ever reached.
"""

from __future__ import annotations

import dataclasses
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backend.app.api.execution as execution_module
import backend.app.db.session as db_session
import backend.app.execution.guards as guards
from backend.app.config import Settings, get_settings
from backend.app.data.loader import DataUnavailableError
from backend.app.execution.alpaca_client import AlpacaExecutionClient
from backend.app.execution.guards import enforce_order_guards
from backend.app.main import app
from tests.conftest import FakeMarketClock


def _soon() -> pd.Timestamp:
    """An earnings date ~1 day from wall-clock now - inside the default 48h
    lockout no matter which "now" the implementation uses."""
    return pd.Timestamp.now().normalize() + pd.Timedelta(days=1)


def _unavailable(ticker, *a, **k):
    raise DataUnavailableError("provider down")


def _never(ticker, *a, **k):  # pragma: no cover - must not run
    raise AssertionError("EXIT must not look up earnings/splits")


@pytest.fixture
def calendars(monkeypatch):
    """Sets the earnings/split fetchers on `execution.guards`."""

    def _set(earnings=None, splits=None):
        monkeypatch.setattr(
            guards,
            "fetch_earnings_dates",
            earnings if callable(earnings) else (lambda t, *a, **k: earnings or []),
        )
        monkeypatch.setattr(
            guards,
            "fetch_stock_splits",
            splits if callable(splits) else (lambda t, *a, **k: splits or []),
        )

    return _set


# ---------------------------------------------------------------- unit level


class TestEnforceOrderGuards:
    def test_f1_entry_allowed_in_regular_session_with_no_events(self, calendars):
        calendars()
        result = enforce_order_guards("AAPL", "ENTRY", Settings(), FakeMarketClock())
        assert result.allowed is True
        assert result.reasons == []

    @pytest.mark.parametrize("state", ["CLOSED", "EXTENDED"])
    def test_f1_entry_blocked_outside_regular_session(self, calendars, state):
        calendars()
        result = enforce_order_guards(
            "AAPL", "ENTRY", Settings(), FakeMarketClock(state)
        )
        assert result.allowed is False
        assert result.reasons

    def test_f1_closed_reason_keeps_closed_wording(self, calendars):
        calendars()
        result = enforce_order_guards(
            "AAPL", "ENTRY", Settings(), FakeMarketClock("CLOSED")
        )
        assert "closed" in " ".join(result.reasons).lower()

    def test_f1_entry_blocked_when_calendar_lookup_raises(self, calendars):
        calendars()
        clock = FakeMarketClock(raises=ConnectionError("alpaca calendar down"))
        result = enforce_order_guards("AAPL", "ENTRY", Settings(), clock)
        assert result.allowed is False
        assert result.reasons

    def test_f1_entry_fails_closed_when_earnings_unavailable(self, calendars):
        calendars(earnings=_unavailable)
        result = enforce_order_guards("AAPL", "ENTRY", Settings(), FakeMarketClock())
        assert result.allowed is False
        assert "unavailable" in " ".join(result.reasons).lower()

    def test_f1_entry_fails_closed_when_splits_unavailable(self, calendars):
        calendars(splits=_unavailable)
        result = enforce_order_guards("AAPL", "ENTRY", Settings(), FakeMarketClock())
        assert result.allowed is False
        assert "unavailable" in " ".join(result.reasons).lower()

    def test_f1_entry_blocked_inside_earnings_lockout(self, calendars):
        calendars(earnings=[_soon()])
        result = enforce_order_guards("AAPL", "ENTRY", Settings(), FakeMarketClock())
        assert result.allowed is False
        assert "earnings" in " ".join(result.reasons).lower()

    def test_f1_entry_blocked_inside_split_lockout(self, calendars):
        calendars(splits=[_soon()])
        result = enforce_order_guards("AAPL", "ENTRY", Settings(), FakeMarketClock())
        assert result.allowed is False
        assert "split" in " ".join(result.reasons).lower()

    def test_f1_far_earnings_does_not_block(self, calendars):
        calendars(earnings=[pd.Timestamp.now().normalize() + pd.Timedelta(days=30)])
        result = enforce_order_guards("AAPL", "ENTRY", Settings(), FakeMarketClock())
        assert result.allowed is True

    def test_f1_exit_always_allowed_and_does_no_lookups(self, calendars):
        calendars(earnings=_never, splits=_never)
        clock = FakeMarketClock("CLOSED", raises=AssertionError("clock called"))
        result = enforce_order_guards("AAPL", "EXIT", Settings(), clock)
        assert result.allowed is True
        assert clock.calls == 0


# ------------------------------------------------------------- settings flag


class TestGuardSwitchRemoved:
    def test_f1_settings_has_no_guards_enabled_field(self):
        names = {f.name for f in dataclasses.fields(Settings)}
        assert "execution_guards_enabled" not in names

    def test_f1_env_var_is_ignored(self, monkeypatch):
        monkeypatch.setenv("EXECUTION_GUARDS_ENABLED", "false")
        get_settings.cache_clear()
        try:
            assert not hasattr(get_settings(), "execution_guards_enabled")
        finally:
            get_settings.cache_clear()

    def test_f1_settings_repr_masks_telegram_chat_id(self):
        settings = Settings(telegram_bot_token="tok-xyz", telegram_chat_id="98765")
        text = repr(settings)
        assert "tok-xyz" not in text
        assert "98765" not in text


# ------------------------------------------------- earnings fetch fail-closed


class _FakeYfTicker:
    """yfinance `Ticker` stand-in: `get_earnings_dates` returns `None`, which
    is what yfinance 1.8 does when it swallows a scrape failure (it logs
    "No earnings dates found" and returns None instead of raising)."""

    def __init__(self, symbol, quote_type="EQUITY"):
        self.ticker = symbol
        self._quote_type = quote_type
        self.info = {"quoteType": quote_type, "symbol": symbol}
        self.fast_info = {"quoteType": quote_type}

    def get_earnings_dates(self, limit=12, offset=0):
        return None

    def get_info(self):
        return self.info


@pytest.fixture
def yf_none(monkeypatch):
    """Every `yfinance.Ticker` returns None for earnings; ETF symbols
    (`SPY`, `QQQ`) report quoteType ETF, everything else EQUITY."""
    import yfinance

    etfs = {"SPY", "QQQ"}
    monkeypatch.setattr(
        yfinance,
        "Ticker",
        lambda s, *a, **k: _FakeYfTicker(
            s.upper(), "ETF" if s.upper() in etfs else "EQUITY"
        ),
    )


class TestEarningsFetchFailClosed:
    def test_f1_loader_none_for_stock_is_unavailable(self, yf_none):
        from backend.app.data.loader import fetch_earnings_dates

        with pytest.raises(DataUnavailableError):
            fetch_earnings_dates("AAPL")

    def test_f1_loader_none_for_etf_is_empty(self, yf_none):
        from backend.app.data.loader import fetch_earnings_dates

        assert fetch_earnings_dates("SPY") == []

    def test_f1_guard_blocks_stock_entry_when_yfinance_returns_none(
        self, yf_none, monkeypatch
    ):
        from backend.app.data import loader

        monkeypatch.setattr(guards, "fetch_earnings_dates", loader.fetch_earnings_dates)
        monkeypatch.setattr(guards, "fetch_stock_splits", lambda t, *a, **k: [])
        result = enforce_order_guards("AAPL", "ENTRY", Settings(), FakeMarketClock())
        assert result.allowed is False
        assert "unavailable" in " ".join(result.reasons).lower()


# --------------------------------------------------------------- API wiring


class _CountingTradingClient:
    def __init__(self, positions=None):
        self.submits = 0
        self.positions = positions if positions is not None else []

    def get_all_positions(self):
        return list(self.positions)

    def submit_order(self, order_data):
        from types import SimpleNamespace

        self.submits += 1
        v = SimpleNamespace
        return v(
            id="o-1",
            symbol="AAPL",
            qty="1",
            side=v(value="buy"),
            type=v(value="market"),
            order_class=v(value="simple"),
            status=v(value="accepted"),
            submitted_at=None,
        )

    def close_all_positions(self, cancel_orders=None):
        return []


@pytest.fixture
def api(market, tmp_path, monkeypatch):
    """TestClient with a REGULAR fake clock, a counting Alpaca SDK client on
    `/execution/*`, and an isolated DuckDB for `/orders/*`."""
    monkeypatch.setenv("SCANS_DB_PATH", str(tmp_path / "orders.duckdb"))
    db_session.reset_for_tests()
    sdk = _CountingTradingClient()
    app.dependency_overrides[get_settings] = lambda: Settings()
    app.dependency_overrides[execution_module._client] = lambda: (
        AlpacaExecutionClient("key", "secret", client=sdk)
    )
    yield TestClient(app), sdk, market
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(execution_module._client, None)
    db_session.reset_for_tests()


def _detail(response) -> str:
    return json.dumps(response.json().get("detail")).lower()


_EXEC_URL = "/api/v1/execution/orders"


_HELD: list[dict] = []  # positions the `broker_spy` Alpaca fake reports


def _position(symbol="AAPL", qty=5.0, side="long"):
    from types import SimpleNamespace

    return SimpleNamespace(
        symbol=symbol,
        side=SimpleNamespace(value=side),
        qty=str(qty),
        avg_entry_price="100",
        current_price="100",
        market_value=str(100 * qty),
        cost_basis=str(100 * qty),
        unrealized_pl="0",
        unrealized_plpc="0",
    )


@pytest.fixture
def held(monkeypatch):
    """`held(sdk, symbol, qty, side)` gives the Alpaca account one open
    position - on the SDK fake and on every `AlpacaExecutionClient`, however
    the route builds it - so a sell can be recognised as an exit."""

    def _set(sdk=None, symbol="AAPL", qty=5.0, side="long"):
        position = _position(symbol, qty, side)
        if sdk is not None:
            sdk.positions = [position]
        from backend.app.execution.alpaca_client import _position_to_dict

        monkeypatch.setattr(
            AlpacaExecutionClient,
            "get_positions",
            lambda self: [_position_to_dict(position)],
        )
        _HELD[:] = [_position_to_dict(position)]

    _HELD.clear()
    yield _set
    _HELD.clear()


def _exec_body(side="buy", **extra):
    return {"ticker": "AAPL", "side": side, "qty": 1, "order_type": "MARKET", **extra}


class TestExecutionOrdersRoute:
    def test_f1_entry_allowed_reaches_broker(self, api):
        client, sdk, _ = api
        assert client.post(_EXEC_URL, json=_exec_body()).status_code == 200
        assert sdk.submits == 1

    def test_f1_closed_market_buy_is_422_and_never_submitted(self, api):
        client, sdk, clock = api
        clock.state = "CLOSED"
        response = client.post(_EXEC_URL, json=_exec_body())
        assert response.status_code == 422
        assert "closed" in _detail(response)
        assert sdk.submits == 0

    def test_f1_calendar_down_blocks_entry(self, api):
        client, sdk, clock = api
        clock.raises = ConnectionError("alpaca down")
        assert client.post(_EXEC_URL, json=_exec_body()).status_code == 422
        assert sdk.submits == 0

    def test_f1_earnings_unavailable_blocks_entry(self, api, monkeypatch):
        client, sdk, _ = api
        monkeypatch.setattr(guards, "fetch_earnings_dates", _unavailable)
        response = client.post(_EXEC_URL, json=_exec_body())
        assert response.status_code == 422
        assert "unavailable" in _detail(response)
        assert sdk.submits == 0

    def test_f1_sell_of_held_long_is_exit_and_goes_through_when_closed(self, api, held):
        client, sdk, clock = api
        held(sdk, qty=5)
        clock.state = "CLOSED"
        assert client.post(_EXEC_URL, json=_exec_body("sell")).status_code == 200
        assert sdk.submits == 1

    def test_f1_sell_of_unheld_symbol_is_guarded_when_closed(self, api, held):
        client, sdk, clock = api
        held(sdk, symbol="MSFT", qty=5)
        clock.state = "CLOSED"
        response = client.post(_EXEC_URL, json=_exec_body("sell"))
        assert response.status_code == 422
        assert sdk.submits == 0

    def test_f1_sell_more_than_held_is_guarded_when_closed(self, api, held):
        client, sdk, clock = api
        held(sdk, qty=1)
        clock.state = "CLOSED"
        body = _exec_body("sell", qty=5)
        assert client.post(_EXEC_URL, json=body).status_code == 422
        assert sdk.submits == 0

    def test_f1_sell_against_short_position_is_guarded_when_closed(self, api, held):
        client, sdk, clock = api
        held(sdk, qty=-5, side="short")
        clock.state = "CLOSED"
        assert client.post(_EXEC_URL, json=_exec_body("sell")).status_code == 422
        assert sdk.submits == 0

    def test_f1_explicit_exit_on_unheld_sell_is_guarded_when_closed(self, api):
        client, sdk, clock = api
        clock.state = "CLOSED"
        body = _exec_body("sell", intent="EXIT")
        assert client.post(_EXEC_URL, json=body).status_code == 422
        assert sdk.submits == 0

    def test_f1_sell_is_guarded_when_position_lookup_fails(self, api, monkeypatch):
        client, sdk, clock = api
        clock.state = "CLOSED"

        def _down():
            raise ConnectionError("alpaca positions down")

        monkeypatch.setattr(sdk, "get_all_positions", _down)
        assert client.post(_EXEC_URL, json=_exec_body("sell")).status_code == 422
        assert sdk.submits == 0

    def test_f1_explicit_exit_intent_on_buy_is_not_let_through(self, api):
        """Reviewer BLOCK: `intent="EXIT"` on a buy bypassed every guard."""
        client, sdk, clock = api
        clock.state = "CLOSED"
        body = _exec_body("buy", intent="EXIT")
        assert client.post(_EXEC_URL, json=body).status_code == 422
        assert sdk.submits == 0

    def test_f1_explicit_entry_intent_on_sell_is_guarded(self, api):
        client, sdk, clock = api
        clock.state = "CLOSED"
        body = _exec_body("sell", intent="ENTRY")
        assert client.post(_EXEC_URL, json=body).status_code == 422
        assert sdk.submits == 0

    def test_f1_invalid_intent_is_422(self, api):
        client, sdk, _ = api
        body = _exec_body(intent="MAYBE")
        assert client.post(_EXEC_URL, json=body).status_code == 422
        assert sdk.submits == 0

    def test_f1_close_all_stays_unguarded(self, api):
        client, _, clock = api
        clock.raises = ConnectionError("alpaca down")
        response = client.post("/api/v1/execution/close-all", json={"confirm": True})
        assert response.status_code == 200
        assert clock.calls == 0


_SUBMIT_URL = "/api/v1/orders/submit"


@pytest.fixture
def broker_spy(monkeypatch):
    """Counts every `PaperBroker.submit`/`AlpacaBroker.submit` call."""
    import backend.app.api.orders as orders_module

    calls: list[str] = []
    for cls in (orders_module.PaperBroker, orders_module.AlpacaBroker):
        original = cls.submit

        def _spy(self, *a, _orig=original, _name=cls.__name__, **k):
            calls.append(_name)
            return _orig(self, *a, **k)

        monkeypatch.setattr(cls, "submit", _spy)

    class _FakeAlpaca:
        def __init__(self, *a, **k):
            self.is_configured = True

        def submit_market_order(self, ticker, qty, side):
            return {"id": "alp-1", "status": "accepted"}

        def submit_limit_order(self, ticker, qty, side, limit_price):
            return {"id": "alp-1", "status": "accepted"}

        def get_positions(self):
            return list(_HELD)

    monkeypatch.setattr(orders_module, "AlpacaExecutionClient", _FakeAlpaca)
    return calls


def _submit_body(broker, side="buy", **extra):
    return {
        "broker": broker,
        "ticker": "AAPL",
        "side": side,
        "qty": 1,
        "order_type": "MARKET",
        "reference_price": 100.0,
        **extra,
    }


class TestOrdersSubmitRoute:
    @pytest.mark.parametrize("broker", ["PAPER", "ALPACA"])
    def test_f1_entry_allowed_reaches_broker(self, api, broker_spy, broker):
        client, _, _ = api
        assert client.post(_SUBMIT_URL, json=_submit_body(broker)).status_code == 200
        assert len(broker_spy) == 1

    @pytest.mark.parametrize("broker", ["PAPER", "ALPACA"])
    def test_f1_closed_market_entry_is_422_never_submitted(
        self, api, broker_spy, broker
    ):
        client, _, clock = api
        clock.state = "CLOSED"
        response = client.post(_SUBMIT_URL, json=_submit_body(broker))
        assert response.status_code == 422
        assert "closed" in _detail(response)
        assert broker_spy == []
        assert client.get("/api/v1/orders/active").json()["orders"] == []

    @pytest.mark.parametrize("broker", ["PAPER", "ALPACA"])
    def test_f1_earnings_lockout_blocks_entry(
        self, api, broker_spy, monkeypatch, broker
    ):
        client, _, _ = api
        monkeypatch.setattr(
            guards, "fetch_earnings_dates", lambda t, *a, **k: [_soon()]
        )
        response = client.post(_SUBMIT_URL, json=_submit_body(broker))
        assert response.status_code == 422
        assert "earnings" in _detail(response)
        assert broker_spy == []

    def test_f1_alpaca_sell_of_held_long_goes_through_when_calendar_down(
        self, api, broker_spy, held
    ):
        client, _, clock = api
        held(qty=5)
        clock.raises = ConnectionError("alpaca down")
        response = client.post(_SUBMIT_URL, json=_submit_body("ALPACA", "sell"))
        assert response.status_code == 200
        assert len(broker_spy) == 1

    def test_f1_alpaca_sell_of_unheld_symbol_is_422_when_closed(
        self, api, broker_spy, held
    ):
        client, _, clock = api
        held(symbol="MSFT", qty=5)
        clock.state = "CLOSED"
        response = client.post(_SUBMIT_URL, json=_submit_body("ALPACA", "sell"))
        assert response.status_code == 422
        assert broker_spy == []

    def test_f1_paper_sell_is_treated_as_entry_when_closed(self, api, broker_spy):
        """PaperBroker keeps no positions, so a PAPER sell can't be proven an
        exit and is guarded like an entry."""
        client, _, clock = api
        clock.state = "CLOSED"
        response = client.post(_SUBMIT_URL, json=_submit_body("PAPER", "sell"))
        assert response.status_code == 422
        assert broker_spy == []

    @pytest.mark.parametrize("broker", ["PAPER", "ALPACA"])
    def test_f1_explicit_exit_intent_on_buy_is_not_let_through(
        self, api, broker_spy, broker
    ):
        client, _, clock = api
        clock.state = "CLOSED"
        body = _submit_body(broker, "buy", intent="EXIT")
        assert client.post(_SUBMIT_URL, json=body).status_code == 422
        assert broker_spy == []

    def test_f1_explicit_entry_intent_on_sell_is_guarded(self, api, broker_spy):
        client, _, clock = api
        clock.state = "EXTENDED"
        body = _submit_body("PAPER", "sell", intent="ENTRY")
        assert client.post(_SUBMIT_URL, json=body).status_code == 422
        assert broker_spy == []

    def test_f1_invalid_intent_is_422(self, api, broker_spy):
        client, _, _ = api
        body = _submit_body("PAPER", intent="MAYBE")
        assert client.post(_SUBMIT_URL, json=body).status_code == 422
        assert broker_spy == []


class TestFailClosedWithoutAlpacaKeys:
    def test_f1_no_keys_blocks_paper_entry_without_network(
        self, tmp_path, monkeypatch, calendars
    ):
        """Real `get_market_clock` with no Alpaca keys: the calendar can't be
        read, so a new entry is refused (by design, per the Gate-1 risk)."""
        import socket

        def _no_network(*a, **k):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket.socket, "connect", _no_network)
        monkeypatch.setenv("SCANS_DB_PATH", str(tmp_path / "orders.duckdb"))
        db_session.reset_for_tests()
        calendars()
        app.dependency_overrides[get_settings] = lambda: Settings()
        try:
            response = TestClient(app).post(_SUBMIT_URL, json=_submit_body("PAPER"))
        finally:
            app.dependency_overrides.pop(get_settings, None)
            db_session.reset_for_tests()
        assert response.status_code == 422
