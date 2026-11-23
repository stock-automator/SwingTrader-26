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


# --------------------------------------------------------------------------
# F2 rework loop 1 (/code-review findings R1-R10)
# --------------------------------------------------------------------------


class SlowTrend(BaseStrategy):
    """Long while Close is above its `lookback`-bar SMA (NaN warm-up -> HOLD)."""

    def __init__(self, lookback=50, sl=0.05, tp=10.0):
        super().__init__("slow_trend")
        self.lookback, self.sl, self.tp = lookback, sl, tp

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        sma = df["Close"].rolling(self.lookback).mean()
        sig = (df["Close"] > sma).astype(int).to_numpy()
        out = pd.DataFrame({"signal": sig}, index=df.index)
        active = out["signal"] != 0
        out["sl_type"] = np.where(active, "PERCENTAGE", None)
        out["sl_value"] = np.where(active, self.sl, np.nan)
        out["tp_type"] = np.where(active, "PERCENTAGE", None)
        out["tp_value"] = np.where(active, self.tp, np.nan)
        return out


def _total_return_pct(result) -> float:
    equity = result.equity_curve["Equity"]
    return (float(equity.iloc[-1]) / float(equity.iloc[0]) - 1) * 100


class TestF2R1OosWarmUp:
    """R1: OOS signals see warm-up history; equity is measured on OOS only."""

    KW = dict(initial_capital=10_000, commission=0.0, slippage_pct=0.0)

    def test_f2_r1_slow_lookback_trades_from_first_oos_bar(self):
        from backend.app.quant.walk_forward import _slice_window, oos_summary

        df = _ramp(n=520, start=100.0, step=0.5)
        summary = oos_summary(SlowTrend(50), {"AAA": df}, df, **self.KW)
        first = summary["windows"][0]
        oos = _slice_window(
            df, pd.Timestamp(first["oos_start"]), pd.Timestamp(first["oos_end"])
        )

        def scripted(entry):
            rm = RiskManager(account_equity=10_000, risk_per_trade_pct=0.02)
            strat = Scripted(entries=[entry], sl=0.05, tp=10.0)
            return _total_return_pct(
                run_backtest(strat, oos, rm, commission=0.0, slippage_pct=0.0)
            )

        # backtesting.py's first next() call is bar 1
        from_first_bar, from_bar_50 = scripted(1), scripted(50)
        assert from_first_bar > 3 * from_bar_50 > 0  # fixture sanity
        assert first["oos_return_pct"] >= 0.8 * from_first_bar, (
            f"OOS return {first['oos_return_pct']:.3f}% looks like a cold start "
            f"(from bar 50: {from_bar_50:.3f}%, from bar 1: {from_first_bar:.3f}%)"
        )

    def test_f2_r1_warm_up_gains_are_not_counted_in_oos(self):
        from backend.app.quant.walk_forward import oos_summary

        df = _ramp(n=520, start=100.0, step=0.5)
        summary = oos_summary(SlowTrend(50), {"AAA": df}, df, **self.KW)
        for row in summary["windows"]:
            # A position below 100% notional cannot beat holding the ticker.
            assert row["oos_return_pct"] <= row["spy_oos_return_pct"] + 1e-9


class TestF2R2OpenPositionExposure:
    def test_f2_r2_position_open_at_end_counts_as_exposure(self):
        comp = run_comparison(
            Scripted(entries=[10], sl=0.10, tp=1.0),  # never exits
            {"AAA": _ramp()},
            spy_frame=_ramp(start=400.0, step=1.0),
            initial_capital=10_000,
        )
        # held from bar 11 to bar 59 of 60
        assert comp.summaries[STRATEGY_KEY].exposure_pct == pytest.approx(80, abs=5)
        assert comp.curves[MATCHED_KEY].iloc[-1] > 10_000


class TestF2R3InvestedFraction:
    """R3: matched SPY follows |notional| / sleeve equity, not 0/1."""

    def _comp(self):
        # 1.5% risk / 5% stop -> ~30% of equity, held ~half the bars.
        return run_comparison(
            Scripted(entries=[9], exits=[39], sl=0.05, tp=1.0),
            {"AAA": _ramp(step=0.05)},
            spy_frame=_ramp(start=400.0, step=1.0),
            initial_capital=10_000,
            risk_per_trade_pct=0.015,
            commission=0.0,
            slippage_pct=0.0,
            fractional=True,
        )

    def test_f2_r3_exposure_pct_still_means_bars_in_a_trade(self):
        assert self._comp().summaries[STRATEGY_KEY].exposure_pct == pytest.approx(
            50, abs=5
        )

    def test_f2_r3_avg_invested_pct_is_mean_notional_fraction(self):
        comp = self._comp()
        assert comp.summaries[STRATEGY_KEY].avg_invested_pct == pytest.approx(15, abs=3)
        payload = comparison_to_payload(comp)["summaries"]
        assert payload[STRATEGY_KEY]["avg_invested_pct"] == pytest.approx(15, abs=3)
        assert payload[MATCHED_KEY]["avg_invested_pct"] == pytest.approx(15, abs=3)

    def test_f2_r3_matched_curve_scales_with_notional(self):
        from backend.app.quant.backtest import exposure_matched_curve

        comp = self._comp()
        spy = comp.curves[SPY_KEY]
        full = exposure_matched_curve(
            spy,
            pd.Series(
                [1.0 if 10 <= i < 40 else 0.0 for i in range(len(spy))],
                index=spy.index,
            ),
            10_000,
        )
        full_gain = full.iloc[-1] - 10_000
        matched_gain = comp.curves[MATCHED_KEY].iloc[-1] - 10_000
        assert full_gain > 0
        assert 0.2 * full_gain < matched_gain < 0.4 * full_gain


class TestF2R4RiskGateWarning:
    def _warnings(self, use_risk_gate):
        return run_comparison(
            Scripted([10], [40], sl=0.05, tp=0.5),
            {"AAA": _ramp(), "BBB": _ramp(start=50.0)},
            spy_frame=_ramp(),
            initial_capital=10_000,
            use_risk_gate=use_risk_gate,
        ).warnings

    def test_f2_r4_risk_gate_run_says_portfolio_cap_not_modelled(self):
        warnings = self._warnings(True)
        assert any(
            "portfolio risk cap" in w.lower() and "dynamic risk" in w.lower()
            for w in warnings
        ), warnings

    def test_f2_r4_no_gate_warning_without_gate(self):
        assert not _has(self._warnings(False), "portfolio risk cap")


class TestF2R5ApiWalkForwardFailures:
    def test_f2_r5_no_common_dates_still_returns_main_result(self, api_prices):
        api_prices["MSFT"] = _frame(
            100 + np.arange(300) * 0.1, start="2026-01-01"
        )  # after AAPL's last bar
        resp = TestClient(app).post(
            "/api/v1/backtest",
            json={
                "strategy": "moving_average_cross",
                "tickers": ["AAPL", "MSFT"],
                "walk_forward": True,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["oos_summary"] is None
        assert _has(body["warnings"], "walk-forward")
        assert body["summaries"][STRATEGY_KEY]["final_value"] > 0

    def test_f2_r5_range_too_short_for_any_window_warns(self, api_prices):
        api_prices["AAPL"] = api_prices["AAPL"].iloc[:200]
        resp = TestClient(app).post(
            "/api/v1/backtest",
            json={
                "strategy": "moving_average_cross",
                "tickers": ["AAPL"],
                "walk_forward": True,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["oos_summary"] is None or body["oos_summary"]["windows"] == []
        assert _has(body["warnings"], "walk-forward"), body["warnings"]


class TestF2R6WindowErrors:
    def test_f2_r6_programming_error_propagates(self):
        from backend.app.quant.walk_forward import _window_return_pct

        with pytest.raises(TypeError):
            _window_return_pct(
                Scripted([5]), _ramp(), 1000, 0.02, 0.0, 0.0, {"no_such_kwarg": 1}
            )

    def test_f2_r6_expected_short_data_still_gives_none(self):
        from backend.app.quant.walk_forward import _window_return_pct

        class Raises(Scripted):
            def generate_signals(self, df):
                raise ValueError("not enough bars for the lookback")

        assert _window_return_pct(Scripted([]), _ramp(n=1), 1000, 0.02, 0, 0) is None
        assert _window_return_pct(Raises([]), _ramp(), 1000, 0.02, 0, 0) is None


class TestF2R7CashCap:
    """R7: oversized orders are capped to cash, never silently cancelled."""

    NO_COST = dict(commission=0.0, slippage_pct=0.0)

    @pytest.mark.parametrize("use_risk_gate", [False, True])
    def test_f2_r7_whole_share_order_capped_to_cash(self, use_risk_gate):
        # $20 risk / $0.495 stop distance -> 40 shares ($3,960) > $1,000 cash
        rm = RiskManager(account_equity=1000, risk_per_trade_pct=0.02)
        result = run_backtest(
            Scripted(entries=[5], sl=0.005, tp=0.5),
            _frame([99.0] * 30),
            rm,
            use_risk_gate=use_risk_gate,
            **self.NO_COST,
        )
        assert not result.trades.empty, "oversized order silently cancelled"
        assert result.trades.iloc[0]["Size"] == 10  # floor(1000 / 99)
        capped = {k: v for k, v in result.skipped_signals.items() if "capped" in k}
        assert sum(capped.values()) == 1, result.skipped_signals

    def test_f2_r7_fractional_order_capped_in_steps(self):
        rm = RiskManager(account_equity=1000, risk_per_trade_pct=0.02)
        result = run_backtest(
            Scripted(entries=[5], sl=0.001, tp=0.5),
            _frame([2000.0] * 30),
            rm,
            fractional=True,
            **self.NO_COST,
        )
        assert not result.trades.empty, "oversized order silently cancelled"
        assert result.trades.iloc[0]["Size"] == pytest.approx(0.5, abs=1e-4)
        assert any("capped" in k for k in result.skipped_signals)

    def test_f2_r7_zero_size_after_cap_is_insufficient_cash(self):
        rm = RiskManager(account_equity=1000, risk_per_trade_pct=0.02)
        result = run_backtest(
            Scripted(entries=[5], sl=0.001, tp=0.5),
            _frame([2000.0] * 30),
            rm,
            **self.NO_COST,
        )
        assert result.trades.empty
        assert result.skipped_signals.get("insufficient cash") == 1


class TestF2R8FractionalKeepsRiskManager:
    def test_f2_r8_subclass_position_size_still_used_when_fractional(self):
        class HalfSize(RiskManager):
            def position_size(self, entry_price, stop_loss_price):
                return super().position_size(entry_price, stop_loss_price) // 2

        result = run_backtest(
            Scripted(entries=[5]),
            _expensive_stop_out(),
            HalfSize(account_equity=1000, risk_per_trade_pct=0.02),
            commission=0.0,
            slippage_pct=0.0,
            fractional=True,
        )
        assert len(result.trades) == 1
        assert result.trades.iloc[0]["Size"] == pytest.approx(0.1, abs=1e-4)


class TestF2R9SurvivorshipWording:
    def test_f2_r9_warning_is_conditional_not_a_membership_claim(self):
        comp = run_comparison(Scripted([10], [40]), {"AAA": _ramp()}, spy_frame=_ramp())
        (warning,) = [w for w in comp.warnings if "survivorship" in w.lower()]
        text = warning.lower()
        assert "tickers are today's index members" not in text
        assert "if these tickers" in text


class TestF2R10BaselineCostsMatchApi:
    def test_f2_r10_same_config_same_final_equity(self, api_prices, monkeypatch):
        import backend.app.config as config_module
        from backend.app.config import Settings, get_settings

        i = np.arange(600)  # oscillating trend: several MA crosses, all costs bite
        api_prices["AAPL"] = _frame(100 + 0.05 * i + 15 * np.sin(2 * np.pi * i / 120))
        settings = Settings(fx_cost_pct=0.0015, fx_conversions_per_round_trip=2)
        monkeypatch.setattr(config_module, "get_settings", lambda: settings)
        app.dependency_overrides[get_settings] = lambda: settings
        config = {
            "strategy": "moving_average_cross",
            "strategy_params": {"sl_pct": 0.2},
            "tickers": ["AAPL"],
            "start": None,
            "end": None,
            "initial_capital": 10_000,
            "risk_per_trade_pct": 0.02,
            "fee_per_share": 0.005,
            "atr_slippage_multiple": 0.1,
            "fractional": True,
            "use_risk_gate": False,
        }
        try:
            resp = TestClient(app).post(
                "/api/v1/backtest",
                json={k: v for k, v in config.items() if v is not None},
            )
        finally:
            app.dependency_overrides.pop(get_settings, None)
        assert resp.status_code == 200, resp.text
        api_body = resp.json()
        assert api_body["costs"]["fx_cost_pct_per_leg"] == pytest.approx(0.0015)
        assert len(api_body["trades"]) > 0

        module = _load_baseline_module()
        baseline = module.run_baseline(
            config, {"AAPL": api_prices["AAPL"]}, api_prices["SPY"]
        )
        assert baseline["summaries"][STRATEGY_KEY]["final_value"] == pytest.approx(
            api_body["summaries"][STRATEGY_KEY]["final_value"], abs=0.01
        )
