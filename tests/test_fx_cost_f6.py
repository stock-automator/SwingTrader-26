"""
Batch 02 F6 - UK FX cost and GBP display.

A keeps a USD balance, so the default is 0 FX conversions per round trip
(no FX cost in backtests or tickets). `FX_COST_PCT` (default 0.15%) applies
per conversion when `FX_CONVERSIONS_PER_ROUND_TRIP` is raised. GBP figures
come from cached `GBPUSD=X` daily closes (USD per 1 GBP), read through
`backend.app.data.fx.load_prices` - the only seam mocked here, so nothing
touches the network or the tracked `data/raw/` cache.
"""

from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.data.loader import DataUnavailableError
from backend.app.main import app
from backend.app.quant.engine import run_backtest
from backend.app.quant.risk import RiskManager
from backend.app.quant.strategies.base import BaseStrategy

FX = 0.0015


class OneRoundTrip(BaseStrategy):
    """Long at bar 10, exit signal at bar 40; stop/target never hit."""

    def __init__(self):
        super().__init__("one_round_trip")

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index)
        out["signal"] = 0
        out.iloc[10, 0] = 1
        out.iloc[40, 0] = -1
        out["sl_type"] = "PERCENTAGE"
        out["sl_value"] = 0.10
        out["tp_type"] = "PERCENTAGE"
        out["tp_value"] = 1.0
        return out


@pytest.fixture
def ramp() -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=60, freq="B")
    close = pd.Series([100.0 + 0.5 * i for i in range(60)], index=idx)
    return pd.DataFrame(
        {
            "Open": close - 0.25,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=idx,
    )


def _run(df, **kw):
    return run_backtest(OneRoundTrip(), df, RiskManager(10_000.0, 0.02), **kw)


# --- Settings -------------------------------------------------------------


class TestF6Settings:
    def test_f6_settings_defaults_usd_balance(self):
        s = Settings()
        assert s.fx_cost_pct == 0.0015
        assert s.fx_conversions_per_round_trip == 0

    def test_f6_settings_read_from_env(self, monkeypatch):
        monkeypatch.setenv("FX_COST_PCT", "0.002")
        monkeypatch.setenv("FX_CONVERSIONS_PER_ROUND_TRIP", "2")
        get_settings.cache_clear()
        try:
            s = get_settings()
            assert s.fx_cost_pct == 0.002
            assert s.fx_conversions_per_round_trip == 2
        finally:
            get_settings.cache_clear()


# --- Engine ---------------------------------------------------------------


class TestF6EngineFxCost:
    # Snapshot taken before F6 (backtesting 0.6.6): one 19-share round trip.
    BASELINE = {
        "commission": (19, 105.30262499999999, 120.25, 279.7146251250001),
        "fee_per_share": (19, 105.30262499999999, 120.25, 283.81012500000014),
    }

    @pytest.mark.parametrize("mode", ["commission", "fee_per_share"])
    def test_f6_fx_zero_reproduces_pre_f6_numbers(self, ramp, mode):
        kw = {"fee_per_share": 0.005} if mode == "fee_per_share" else {}
        result = _run(ramp, fx_cost_pct=0.0, **kw)
        assert len(result.trades) == 1
        t = result.trades.iloc[0]
        size, entry, exit_, pnl = self.BASELINE[mode]
        assert t["Size"] == size
        assert t["EntryPrice"] == pytest.approx(entry, abs=1e-9)
        assert t["ExitPrice"] == pytest.approx(exit_, abs=1e-9)
        assert t["PnL"] == pytest.approx(pnl, abs=1e-9)
        # The default must be fx=0 too.
        pd.testing.assert_frame_equal(result.trades, _run(ramp, **kw).trades)

    @pytest.mark.parametrize("mode", ["commission", "fee_per_share"])
    def test_f6_fx_cost_stacks_on_commission_per_leg(self, ramp, mode):
        kw = {"fee_per_share": 0.005} if mode == "fee_per_share" else {}
        base = _run(ramp, fx_cost_pct=0.0, **kw)
        fx = _run(ramp, fx_cost_pct=FX, **kw)
        b, f = base.trades.iloc[0], fx.trades.iloc[0]
        # Same fills: FX only adds cost, it does not move prices or size.
        assert f["Size"] == b["Size"]
        assert f["EntryPrice"] == pytest.approx(b["EntryPrice"])
        assert f["ExitPrice"] == pytest.approx(b["ExitPrice"])
        size = abs(b["Size"])
        expected = FX * size * (b["EntryPrice"] + b["ExitPrice"])
        # ~= 2 * FX * notional; exact per-leg sum from the trades frame.
        assert b["PnL"] - f["PnL"] == pytest.approx(expected, abs=1e-9)
        eq_b = base.equity_curve["Equity"].iloc[-1]
        eq_f = fx.equity_curve["Equity"].iloc[-1]
        assert eq_b - eq_f == pytest.approx(expected, abs=1e-6)

    def test_f6_fx_cost_with_no_trades_is_harmless(self, ramp):
        class Flat(OneRoundTrip):
            def generate_signals(self, df):
                out = super().generate_signals(df)
                out["signal"] = 0
                return out

        result = run_backtest(Flat(), ramp, RiskManager(10_000.0), fx_cost_pct=FX)
        assert result.trades.empty
        assert result.equity_curve["Equity"].iloc[-1] == pytest.approx(10_000.0)


# --- Backtest API ---------------------------------------------------------


@pytest.fixture
def api(monkeypatch, ramp, tmp_path):
    """TestClient with synthetic prices and a capture of every
    `run_backtest` call's `fx_cost_pct`."""
    import backend.app.api.backtest as backtest_module
    import backend.app.api.deps as deps_module
    import backend.app.quant.backtest as qb

    def _load_prices(ticker, start=None, end=None, data_dir=None, allow_download=True):
        if ticker.upper() not in {"AAPL", "SPY"}:
            raise DataUnavailableError(ticker)
        return ramp

    monkeypatch.setattr(deps_module, "load_prices", _load_prices)
    monkeypatch.setattr(backtest_module, "load_prices", _load_prices)

    seen: list = []
    real = qb.run_backtest

    def _spy(*args, **kwargs):
        seen.append(kwargs.get("fx_cost_pct", "MISSING"))
        return real(*args, **kwargs)

    monkeypatch.setattr(qb, "run_backtest", _spy)

    def use(**settings_kw):
        settings = Settings(allow_downloads=False, data_dir=tmp_path, **settings_kw)
        app.dependency_overrides[get_settings] = lambda: settings

    use()
    yield TestClient(app), use, seen
    app.dependency_overrides.pop(get_settings, None)


def _post_backtest(client, **extra):
    return client.post(
        "/api/v1/backtest",
        json={"strategy": "moving_average_cross", "tickers": ["AAPL"], **extra},
    )


class TestF6BacktestApi:
    def test_f6_default_usd_balance_means_zero_fx(self, api):
        client, _, seen = api
        r = _post_backtest(client)
        assert r.status_code == 200, r.text
        costs = r.json()["costs"]
        assert costs["fx_cost_pct_per_leg"] == 0.0
        assert costs["commission"] == 0.001
        assert costs["slippage_pct"] == 0.0005
        assert seen and all(v == 0.0 for v in seen)

    def test_f6_conversions_setting_drives_per_leg_rate(self, api):
        client, use, seen = api
        use(fx_cost_pct=FX, fx_conversions_per_round_trip=2)
        r = _post_backtest(client)
        assert r.status_code == 200, r.text
        assert r.json()["costs"]["fx_cost_pct_per_leg"] == pytest.approx(FX)
        assert seen and all(v == pytest.approx(FX) for v in seen)

    def test_f6_one_conversion_is_half_per_leg(self, api):
        client, use, _ = api
        use(fx_cost_pct=FX, fx_conversions_per_round_trip=1)
        r = _post_backtest(client)
        assert r.json()["costs"]["fx_cost_pct_per_leg"] == pytest.approx(FX / 2)

    @pytest.mark.parametrize("value", [0.0, 0.002])
    def test_f6_request_overrides_settings(self, api, value):
        client, use, seen = api
        use(fx_cost_pct=FX, fx_conversions_per_round_trip=2)
        r = _post_backtest(client, fx_cost_pct=value)
        assert r.status_code == 200, r.text
        assert r.json()["costs"]["fx_cost_pct_per_leg"] == pytest.approx(value)
        assert seen and all(v == pytest.approx(value) for v in seen)

    def test_f6_negative_fx_is_422(self, api):
        client, _, _ = api
        assert _post_backtest(client, fx_cost_pct=-0.001).status_code == 422


# --- usd_to_gbp -----------------------------------------------------------


def _gbpusd(closes: dict[str, float]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in closes])
    c = pd.Series(list(closes.values()), index=idx)
    return pd.DataFrame(
        {"Open": c, "High": c, "Low": c, "Close": c, "Volume": 0.0}, index=idx
    )


@pytest.fixture
def fx_rates(monkeypatch):
    """Serve `GBPUSD=X` from memory; `state["frame"]`/`state["raises"]`
    let a test swap the series or simulate an uncached/unavailable rate."""
    state = {
        "frame": _gbpusd({"2026-11-19": 1.25, "2026-11-20": 1.30, "2026-11-23": 1.28}),
        "raises": None,
        "tickers": [],
    }

    def _load_prices(ticker, *args, **kwargs):
        state["tickers"].append(ticker)
        if state["raises"] is not None:
            raise state["raises"]
        if ticker.upper() != "GBPUSD=X":
            raise DataUnavailableError(ticker)
        return state["frame"]

    monkeypatch.setattr("backend.app.data.fx.load_prices", _load_prices)
    return state


class TestF6UsdToGbp:
    def test_f6_latest_rate_when_no_as_of(self, fx_rates):
        from backend.app.data.fx import usd_to_gbp

        gbp, rate, as_of = usd_to_gbp(128.0)
        assert rate == 1.28
        assert gbp == pytest.approx(100.0)
        assert as_of == date(2026, 11, 23)
        assert [t.upper() for t in fx_rates["tickers"]] == ["GBPUSD=X"]

    def test_f6_weekend_as_of_uses_prior_friday(self, fx_rates):
        from backend.app.data.fx import usd_to_gbp

        gbp, rate, as_of = usd_to_gbp(130.0, as_of=date(2026, 11, 22))
        assert (rate, as_of) == (1.30, date(2026, 11, 20))
        assert gbp == pytest.approx(100.0)

    def test_f6_as_of_before_history_is_none(self, fx_rates):
        from backend.app.data.fx import usd_to_gbp

        assert usd_to_gbp(100.0, as_of=date(2020, 1, 1)) is None

    def test_f6_trailing_nan_close_is_skipped(self, fx_rates):
        from backend.app.data.fx import usd_to_gbp

        fx_rates["frame"] = _gbpusd({"2026-11-20": 1.30, "2026-11-23": float("nan")})
        _, rate, as_of = usd_to_gbp(1.0)
        assert (rate, as_of) == (1.30, date(2026, 11, 20))

    @pytest.mark.parametrize(
        "raises",
        [DataUnavailableError("GBPUSD=X not cached"), OSError("network down")],
    )
    def test_f6_unavailable_rate_is_none(self, fx_rates, raises):
        from backend.app.data.fx import usd_to_gbp

        fx_rates["raises"] = raises
        assert usd_to_gbp(100.0) is None

    def test_f6_empty_series_is_none(self, fx_rates):
        from backend.app.data.fx import usd_to_gbp

        fx_rates["frame"] = _gbpusd({})
        assert usd_to_gbp(100.0) is None


# --- Order ticket ---------------------------------------------------------

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
def ticket_client(fx_rates, tmp_path):
    def use(**settings_kw):
        settings = Settings(allow_downloads=False, data_dir=tmp_path, **settings_kw)
        app.dependency_overrides[get_settings] = lambda: settings

    use()
    yield TestClient(app), use
    app.dependency_overrides.pop(get_settings, None)


def _ticket(client):
    r = client.post("/api/v1/order-ticket", json=TICKET)
    assert r.status_code == 200, r.text
    (t,) = r.json()["tickets"]
    assert t["quantity"] > 0
    return t


class TestF6OrderTicket:
    def test_f6_ticket_fx_cost_zero_by_default(self, ticket_client):
        client, _ = ticket_client
        assert _ticket(client)["fx_cost_usd"] == 0.0

    def test_f6_ticket_fx_cost_with_two_conversions(self, ticket_client):
        client, use = ticket_client
        use(fx_cost_pct=FX, fx_conversions_per_round_trip=2)
        t = _ticket(client)
        assert t["fx_cost_usd"] == pytest.approx(t["notional_value"] * FX * 2, abs=0.01)

    def test_f6_ticket_gbp_figures_from_latest_rate(self, ticket_client):
        client, _ = ticket_client
        t = _ticket(client)
        assert t["risk_gbp"] == pytest.approx(t["risk_amount"] / 1.28, abs=0.01)
        assert t["notional_gbp"] == pytest.approx(t["notional_value"] / 1.28, abs=0.01)

    def test_f6_ticket_gbp_null_with_note_when_no_rate(self, ticket_client, fx_rates):
        client, _ = ticket_client
        fx_rates["raises"] = DataUnavailableError("GBPUSD=X not cached")
        t = _ticket(client)
        assert t["risk_gbp"] is None
        assert t["notional_gbp"] is None
        assert t["fx_cost_usd"] == 0.0
        notes = " ".join(str(v) for k, v in t.items() if "note" in k and v)
        assert "GBP" in notes
        assert t["tradable"] is True  # a missing rate never blocks a ticket
