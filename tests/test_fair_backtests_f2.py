"""
Batch 02 F2 - fair portfolio backtests.

Covers: backtest-only fractional sizing (`run_backtest(fractional=True)` via
`backtesting.lib.FractionalBacktest`), the live risk gate in backtests
(`use_risk_gate=True`, rejections counted in `skipped_signals`), a regime
gate that decides bar t from SPY data up to t-1, `exposure_pct` plus an
exposure-matched SPY curve, honest-result warnings, walk-forward-first API
output and a reproducible baseline run.

All data is synthetic; nothing touches the network or `data/raw/`.
"""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.data.loader import DataUnavailableError
from backend.app.main import app
from backend.app.quant.backtest import (
    BUY_HOLD_KEY,
    SPY_KEY,
    STRATEGY_KEY,
    comparison_to_payload,
    run_comparison,
)
from backend.app.quant.engine import run_backtest
from backend.app.quant.gating import RegimeGatedStrategy
from backend.app.quant.risk import RiskManager
from backend.app.quant.strategies import REGISTRY
from backend.app.quant.strategies.base import BaseStrategy
from backend.app.quant.strategies.moving_average_cross import MovingAverageCross

ROOT = Path(__file__).resolve().parent.parent
MATCHED_KEY = "spy_exposure_matched"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


class Scripted(BaseStrategy):
    """Emits `signal` at the given bar positions; everything else HOLD."""

    def __init__(self, entries, exits=(), sl=0.05, tp=0.5, every_bar=False):
        super().__init__("scripted")
        self.entries, self.exits = list(entries), list(exits)
        self.sl, self.tp, self.every_bar = sl, tp, every_bar

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        sig = np.zeros(len(df), dtype=int)
        if self.every_bar:
            sig[:] = 1
        for i in self.entries:
            sig[i] = 1
        for i in self.exits:
            sig[i] = -1
        out = pd.DataFrame({"signal": sig}, index=df.index)
        active = out["signal"] != 0
        out["sl_type"] = np.where(active, "PERCENTAGE", None)
        out["sl_value"] = np.where(active, self.sl, np.nan)
        out["tp_type"] = np.where(active, "PERCENTAGE", None)
        out["tp_value"] = np.where(active, self.tp, np.nan)
        return out


def _frame(close, start="2023-01-02", wick=1.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(close), freq="B")
    close = pd.Series(np.asarray(close, dtype=float), index=idx)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + wick,
            "Low": close - wick,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=idx,
    )


def _ramp(n=60, start=100.0, step=0.5, **kw) -> pd.DataFrame:
    return _frame([start + step * i for i in range(n)], **kw)


def _expensive_stop_out() -> pd.DataFrame:
    """$2,000 stock, flat, then bar 15 trades down to 1,850 (through a 5% stop
    at 1,900) from a 2,000 open."""
    df = _frame([2000.0] * 30)
    df.iloc[15, df.columns.get_loc("Low")] = 1850.0
    return df


# --------------------------------------------------------------------------
# F2.1 Fractional sizing (backtest only)
# --------------------------------------------------------------------------


class TestF2FractionalSizing:
    def test_f2_1_fractional_false_is_bit_identical_to_default(self, ohlcv):
        rm = RiskManager(account_equity=5000, risk_per_trade_pct=0.02)
        base = run_backtest(MovingAverageCross(), ohlcv, rm)
        explicit = run_backtest(MovingAverageCross(), ohlcv, rm, fractional=False)
        pd.testing.assert_frame_equal(base.equity_curve, explicit.equity_curve)
        pd.testing.assert_frame_equal(base.trades, explicit.trades)

    def test_f2_1_whole_shares_take_no_trade_on_expensive_stock(self):
        rm = RiskManager(account_equity=1000, risk_per_trade_pct=0.02)
        result = run_backtest(
            Scripted(entries=[5]),
            _expensive_stop_out(),
            rm,
            commission=0.0,
            slippage_pct=0.0,
        )
        assert result.trades.empty  # $20 risk / $100 per share -> 0 shares

    def test_f2_1_fractional_sizes_by_risk_and_unscales_prices(self):
        rm = RiskManager(account_equity=1000, risk_per_trade_pct=0.02)
        result = run_backtest(
            Scripted(entries=[5]),
            _expensive_stop_out(),
            rm,
            commission=0.0,
            slippage_pct=0.0,
            fractional=True,
        )
        assert len(result.trades) == 1
        trade = result.trades.iloc[0]
        assert trade["Size"] == pytest.approx(0.2, abs=1e-4)
        assert trade["EntryPrice"] == pytest.approx(2000.0, rel=1e-6)
        # the stop is resolved in real prices and scaled with the data
        assert trade["ExitPrice"] == pytest.approx(1900.0, rel=1e-6)
        assert result.equity_curve["Equity"].iloc[-1] == pytest.approx(980.0, abs=0.05)

    def test_f2_1_fractional_per_share_fee_charged_on_real_shares(self):
        rm = RiskManager(account_equity=1000, risk_per_trade_pct=0.02)
        result = run_backtest(
            Scripted(entries=[5]),
            _expensive_stop_out(),
            rm,
            slippage_pct=0.0,
            fee_per_share=0.005,
            fractional=True,
        )
        # 0.2 shares * $0.005 * 2 legs = $0.002, not 2000 units * $0.005 * 2
        assert result.equity_curve["Equity"].iloc[-1] == pytest.approx(
            1000 - 20 - 0.002, abs=0.01
        )

    def test_f2_1_live_order_contract_stays_whole_shares(self):
        rm = RiskManager(account_equity=1000, risk_per_trade_pct=0.02)
        order = rm.build_order(2000.0, "PERCENTAGE", 0.01, "PERCENTAGE", 0.05)
        gated = rm.build_risk_managed_order(
            2000.0, "PERCENTAGE", 0.01, "PERCENTAGE", 0.05
        )
        assert isinstance(order.shares, int) and isinstance(gated.shares, int)


# --------------------------------------------------------------------------
# F2.2 Same risk gate as live
# --------------------------------------------------------------------------


class TestF2RiskGate:
    def test_f2_2_gate_off_by_default_and_skips_nothing(self):
        rm = RiskManager(account_equity=10_000, risk_per_trade_pct=0.02)
        strat = Scripted(entries=[5], exits=[20], sl=0.05, tp=0.05)  # 1R
        result = run_backtest(strat, _ramp(), rm)
        assert len(result.trades) == 1
        assert result.skipped_signals == {}

    def test_f2_2_gate_rejects_below_min_reward_risk_and_counts_reason(self):
        rm = RiskManager(account_equity=10_000, risk_per_trade_pct=0.02)
        strat = Scripted(entries=[5, 25, 45], exits=[20, 40], sl=0.05, tp=0.05)
        result = run_backtest(strat, _ramp(), rm, use_risk_gate=True)
        assert result.trades.empty
        assert sum(result.skipped_signals.values()) == 3
        assert len(result.skipped_signals) == 1
        (reason,) = result.skipped_signals
        assert "reward" in reason.lower()

    def test_f2_2_gate_sizes_like_live_risk_managed_order(self):
        df = _ramp()
        rm = RiskManager(account_equity=10_000, risk_per_trade_pct=0.02)
        # 3R: the live gate's dynamic risk sizes below the flat 2% budget
        strat = Scripted(entries=[5], exits=[20], sl=0.02, tp=0.06)
        result = run_backtest(strat, df, rm, use_risk_gate=True)
        expected = rm.build_risk_managed_order(
            float(df["Close"].iloc[5]), "PERCENTAGE", 0.02, "PERCENTAGE", 0.06
        )
        flat = rm.build_order(
            float(df["Close"].iloc[5]), "PERCENTAGE", 0.02, "PERCENTAGE", 0.06
        )
        assert not expected.rejected and expected.shares < flat.shares
        assert result.skipped_signals == {}
        assert len(result.trades) == 1
        assert result.trades.iloc[0]["Size"] == expected.shares

    def test_f2_2_run_comparison_aggregates_skipped_across_sleeves(self):
        strat = Scripted(entries=[5, 25], exits=[20], sl=0.05, tp=0.05)
        frames = {"AAA": _ramp(), "BBB": _ramp(start=50.0)}
        comp = run_comparison(
            strat, frames, spy_frame=_ramp(), initial_capital=20_000, use_risk_gate=True
        )
        assert sum(comp.skipped_signals.values()) == 4
        payload = comparison_to_payload(comp)
        assert sum(payload["skipped_signals"].values()) == 4


# --------------------------------------------------------------------------
# F2.3 Trend filter uses the prior day's SPY close (no lookahead)
# --------------------------------------------------------------------------


class TestF2RegimeGateNoLookahead:
    T = 250

    @staticmethod
    def _spy(n=280) -> pd.DataFrame:
        # Calm uptrend: BULL_TRENDING once the 200-day SMA is defined.
        return _ramp(n=n, start=300.0, step=0.3)

    def _gate(self, spy: pd.DataFrame) -> pd.Series:
        target = _ramp(n=len(spy), start=50.0, step=0.1)
        gated = RegimeGatedStrategy(Scripted([], every_bar=True), benchmark_df=spy)
        return gated.generate_signals(target)["signal"]

    def test_f2_3_calm_spy_does_not_block(self):
        assert self._gate(self._spy()).iloc[self.T] == 1

    def test_f2_3_changing_spy_close_on_bar_t_does_not_change_gate_on_bar_t(self):
        spy = self._spy()
        crashed = spy.copy()
        crashed.iloc[self.T, crashed.columns.get_loc("Close")] *= 0.5
        assert self._gate(crashed).iloc[self.T] == self._gate(spy).iloc[self.T] == 1

    def test_f2_3_crash_on_bar_t_minus_1_blocks_bar_t(self):
        spy = self._spy()
        spy.iloc[self.T - 1, spy.columns.get_loc("Close")] *= 0.5
        assert self._gate(spy).iloc[self.T] == 0


# --------------------------------------------------------------------------
# F2.4 Exposure % and an exposure-matched SPY line
# --------------------------------------------------------------------------


class TestF2Exposure:
    def _comp(self, strategy):
        return run_comparison(
            strategy,
            {"AAA": _ramp()},
            spy_frame=_ramp(start=400.0, step=1.0),
            initial_capital=10_000,
        )

    def test_f2_4_every_summary_has_exposure_pct(self):
        comp = self._comp(Scripted(entries=[10], exits=[40], sl=0.10, tp=1.0))
        assert comp.summaries[STRATEGY_KEY].exposure_pct == pytest.approx(50, abs=5)
        assert comp.summaries[BUY_HOLD_KEY].exposure_pct == pytest.approx(100)
        assert comp.summaries[SPY_KEY].exposure_pct == pytest.approx(100)
        summaries = comparison_to_payload(comp)["summaries"]
        assert all(0 <= s["exposure_pct"] <= 100 for s in summaries.values())

    def test_f2_4_exposure_matched_spy_sits_between_cash_and_spy(self):
        comp = self._comp(Scripted(entries=[10], exits=[40], sl=0.10, tp=1.0))
        assert MATCHED_KEY in comp.curves.columns
        matched = comp.curves[MATCHED_KEY]
        assert matched.iloc[0] == pytest.approx(10_000)
        assert 10_000 < matched.iloc[-1] < comp.curves[SPY_KEY].iloc[-1]
        assert comp.summaries[MATCHED_KEY].exposure_pct == pytest.approx(
            comp.summaries[STRATEGY_KEY].exposure_pct, abs=1
        )

    def test_f2_4_zero_exposure_gives_flat_matched_curve(self):
        comp = self._comp(Scripted(entries=[]))
        assert comp.summaries[STRATEGY_KEY].exposure_pct == pytest.approx(0)
        assert np.allclose(comp.curves[MATCHED_KEY], 10_000)

    def test_f2_4_no_spy_means_no_matched_curve(self):
        comp = run_comparison(Scripted(entries=[10], exits=[40]), {"AAA": _ramp()})
        assert MATCHED_KEY not in comp.curves.columns


# --------------------------------------------------------------------------
# F2.5 Honest-result warnings
# --------------------------------------------------------------------------


def _has(warnings, needle):
    return any(needle in w.lower() for w in warnings)


class TestF2Warnings:
    def test_f2_5_survivorship_label_always_present(self):
        comp = run_comparison(Scripted([10], [40]), {"AAA": _ramp()}, spy_frame=_ramp())
        assert _has(comp.warnings, "survivorship")
        assert not _has(comp.warnings, "fractional")

    def test_f2_5_fractional_run_is_labelled(self):
        comp = run_comparison(
            Scripted([10], [40]), {"AAA": _ramp()}, spy_frame=_ramp(), fractional=True
        )
        assert _has(comp.warnings, "fractional")

    def test_f2_5_too_few_trades_warning_below_30(self):
        comp = run_comparison(Scripted([10], [40]), {"AAA": _ramp()}, spy_frame=_ramp())
        assert _has(comp.warnings, "too few trades")

    def test_f2_5_no_too_few_warning_with_30_plus_trades(self):
        n = 200
        strat = Scripted(range(0, n - 4, 4), range(2, n - 2, 4), sl=0.10, tp=0.20)
        df = _ramp(n=n, step=0.1, wick=0.5)
        comp = run_comparison(strat, {"AAA": df}, spy_frame=df, initial_capital=10_000)
        assert len(comp.trades) >= 30
        assert not _has(comp.warnings, "too few trades")


# --------------------------------------------------------------------------
# F2.6 API: walk-forward out-of-sample first, new flags wired through
# --------------------------------------------------------------------------


@pytest.fixture
def api_prices(monkeypatch):
    rng = np.random.default_rng(11)
    universe = {
        "AAPL": _frame(100 + np.arange(600) * 0.2 + rng.normal(0, 1, 600).cumsum()),
        "SPY": _frame(300 + np.arange(600) * 0.1 + rng.normal(0, 1, 600).cumsum()),
    }

    def _load_prices(ticker, start=None, end=None, data_dir=None, allow_download=True):
        if ticker.upper() not in universe:
            raise DataUnavailableError(f"{ticker} not in test universe")
        return universe[ticker.upper()]

    import backend.app.api.backtest as backtest_module
    import backend.app.api.deps as deps_module

    monkeypatch.setattr(deps_module, "load_prices", _load_prices)
    monkeypatch.setattr(backtest_module, "load_prices", _load_prices)
    return universe


class TestF2Api:
    BODY = {"strategy": "moving_average_cross", "tickers": ["AAPL"]}

    def test_f2_6_walk_forward_oos_vs_spy_leads_the_response(self, api_prices):
        resp = TestClient(app).post(
            "/api/v1/backtest", json={**self.BODY, "walk_forward": True}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert list(body)[0] == "oos_summary"
        windows = body["oos_summary"]["windows"]
        assert windows
        assert {"oos_return_pct", "spy_oos_return_pct"} <= set(windows[0])

    def test_f2_6_no_walk_forward_by_default(self, api_prices):
        body = TestClient(app).post("/api/v1/backtest", json=self.BODY).json()
        assert body.get("oos_summary") is None

    def test_f2_6_flags_reach_the_response(self, api_prices):
        resp = TestClient(app).post(
            "/api/v1/backtest",
            json={**self.BODY, "fractional": True, "use_risk_gate": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body["skipped_signals"], dict)
        assert "exposure_pct" in body["summaries"][STRATEGY_KEY]
        assert MATCHED_KEY in body["summaries"]
        assert _has(body["warnings"], "fractional")
        assert _has(body["warnings"], "survivorship")


# --------------------------------------------------------------------------
# F2.7 Reproducible baseline
# --------------------------------------------------------------------------


def _load_baseline_module():
    path = ROOT / "scripts" / "run_baseline.py"
    spec = importlib.util.spec_from_file_location("run_baseline_f2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestF2Baseline:
    def test_f2_7_committed_config_pins_a_fixed_window(self):
        config = json.loads((ROOT / "config" / "baseline_backtest.json").read_text())
        assert config["strategy"] in REGISTRY
        assert config["tickers"] and all(isinstance(t, str) for t in config["tickers"])
        assert pd.Timestamp(config["start"]) < pd.Timestamp(config["end"])

    def test_f2_7_baseline_run_is_reproducible(self):
        module = _load_baseline_module()
        config = json.loads((ROOT / "config" / "baseline_backtest.json").read_text())
        rng = np.random.default_rng(5)

        def walk(base, drift):
            return base + np.arange(400) * drift + rng.normal(0, 1, 400).cumsum()

        frames = {
            t: _frame(walk(100, 0.2), start=config["start"])
            for t in config["tickers"][:2]
        }
        spy = _frame(walk(300, 0.1), start=config["start"])
        first = module.run_baseline(config, frames, spy)
        second = module.run_baseline(config, frames, spy)
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
        assert _has(first["warnings"], "survivorship")
