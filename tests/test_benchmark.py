"""
Tests for backend/app/quant/backtest.py - the $1,000 relative-growth
benchmark engine (strategy vs buy & hold vs SPY).
"""

import json
import math

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.backtest import (
    BUY_HOLD_KEY,
    DEFAULT_INITIAL_CAPITAL,
    SPY_KEY,
    STRATEGY_KEY,
    align_curves,
    buy_and_hold_curve,
    comparison_to_payload,
    extract_strategy_equity,
    rebase,
    relative_metrics,
    run_comparison,
    summarize_curve,
)
from backend.app.quant.engine import BacktestResult
from backend.app.quant.strategies.moving_average_cross import MovingAverageCross


def _trending_ohlcv(
    n: int = 300, start: str = "2022-01-03", drift: float = 0.3, seed: int = 7
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range(start, periods=n, freq="B")
    noise = rng.normal(0, 1.0, n).cumsum()
    close = pd.Series(100.0 + np.arange(n) * drift + noise, index=index)
    close = close.clip(lower=5.0)

    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": pd.Series(1_000_000.0, index=index),
        },
        index=index,
    )


class TestDefaults:
    def test_default_initial_capital_is_one_thousand(self):
        assert DEFAULT_INITIAL_CAPITAL == 1000.0


class TestBuyAndHoldCurve:
    def test_scales_to_initial_capital(self):
        prices = pd.Series([50.0, 55.0, 45.0, 60.0])
        curve = buy_and_hold_curve(prices, initial_capital=1000.0)
        assert curve.iloc[0] == pytest.approx(1000.0)
        assert curve.iloc[-1] == pytest.approx(1000.0 * 60.0 / 50.0)

    def test_empty_series_raises(self):
        with pytest.raises(ValueError, match="empty"):
            buy_and_hold_curve(pd.Series(dtype=float), 1000.0)

    def test_non_positive_first_price_raises(self):
        with pytest.raises(ValueError, match="positive and finite"):
            buy_and_hold_curve(pd.Series([0.0, 10.0]), 1000.0)

    def test_nan_first_price_raises(self):
        with pytest.raises(ValueError, match="positive and finite"):
            buy_and_hold_curve(pd.Series([float("nan"), 10.0]), 1000.0)


class TestAlignCurves:
    def test_intersects_indices_and_forward_fills_benchmark(self):
        idx = pd.date_range("2022-01-01", periods=5, freq="D")
        strategy = pd.Series([1000.0, 1010.0, 1020.0, 1030.0, 1040.0], index=idx)
        # Benchmark missing the middle bar (a holiday) - should forward-fill.
        benchmark = pd.Series(
            [500.0, 505.0, 515.0, 520.0],
            index=idx[[0, 1, 3, 4]],
        )
        aligned = align_curves({STRATEGY_KEY: strategy, "bench": benchmark})
        assert list(aligned.index) == list(idx)
        assert aligned["bench"].iloc[2] == pytest.approx(505.0)  # ffilled

    def test_missing_strategy_key_raises(self):
        with pytest.raises(ValueError, match=STRATEGY_KEY):
            align_curves({"bench": pd.Series([1.0, 2.0])})

    def test_no_overlap_raises(self):
        strategy = pd.Series([1.0, 2.0], index=pd.date_range("2022-01-01", periods=2))
        benchmark = pd.Series([1.0, 2.0], index=pd.date_range("2023-01-01", periods=2))
        with pytest.raises(ValueError, match="No overlapping dates"):
            align_curves({STRATEGY_KEY: strategy, "bench": benchmark})


class TestRebase:
    def test_scales_every_column_to_initial_capital(self):
        idx = pd.date_range("2022-01-01", periods=3, freq="D")
        frame = pd.DataFrame(
            {"a": [50.0, 55.0, 60.0], "b": [200.0, 210.0, 220.0]}, index=idx
        )
        rebased = rebase(frame, 1000.0)
        assert rebased["a"].iloc[0] == pytest.approx(1000.0)
        assert rebased["b"].iloc[0] == pytest.approx(1000.0)
        assert rebased["a"].iloc[-1] == pytest.approx(1000.0 * 60 / 50)

    def test_non_positive_anchor_raises(self):
        frame = pd.DataFrame({"a": [0.0, 1.0]})
        with pytest.raises(ValueError, match="cannot rebase"):
            rebase(frame, 1000.0)


class TestSummarizeCurve:
    def test_flat_curve_has_zero_return(self):
        idx = pd.date_range("2022-01-01", periods=50, freq="D")
        curve = pd.Series(1000.0, index=idx)
        summary = summarize_curve(curve, "Flat")
        assert summary.initial_value == 1000.0
        assert summary.final_value == 1000.0
        assert summary.total_return_pct == pytest.approx(0.0)

    def test_growth_curve_total_return(self):
        idx = pd.date_range("2022-01-01", periods=2, freq="D")
        curve = pd.Series([1000.0, 1500.0], index=idx)
        summary = summarize_curve(curve, "Growth")
        assert summary.total_return_pct == pytest.approx(50.0)


class TestExtractStrategyEquity:
    def test_missing_equity_column_raises(self):
        result = BacktestResult(
            stats=pd.Series(dtype=float),
            trades=pd.DataFrame(),
            equity_curve=pd.DataFrame({"NotEquity": [1.0, 2.0]}),
        )
        with pytest.raises(ValueError, match="Equity"):
            extract_strategy_equity(result, 1000.0)

    def test_empty_equity_raises(self):
        result = BacktestResult(
            stats=pd.Series(dtype=float),
            trades=pd.DataFrame(),
            equity_curve=pd.DataFrame({"Equity": pd.Series(dtype=float)}),
        )
        with pytest.raises(ValueError, match="empty"):
            extract_strategy_equity(result, 1000.0)

    def test_extracts_and_renames_equity_column(self):
        idx = pd.date_range("2022-01-01", periods=3, freq="D")
        result = BacktestResult(
            stats=pd.Series(dtype=float),
            trades=pd.DataFrame(),
            equity_curve=pd.DataFrame(
                {"Equity": [1000.0, 1010.0, 1005.0], "DrawdownPct": [0, -0.01, -0.005]},
                index=idx,
            ),
        )
        equity = extract_strategy_equity(result, 1000.0)
        assert equity.name == STRATEGY_KEY
        assert list(equity) == [1000.0, 1010.0, 1005.0]


class TestRelativeMetrics:
    def test_identical_curves_have_beta_one_and_zero_alpha(self):
        idx = pd.date_range("2022-01-01", periods=100, freq="D")
        rng = np.random.default_rng(1)
        curve = pd.Series(
            1000.0 * np.cumprod(1 + rng.normal(0.0003, 0.01, 100)), index=idx
        )
        metrics = relative_metrics(curve, curve, "self")
        assert metrics.beta == pytest.approx(1.0, abs=1e-8)
        assert metrics.alpha_annual_pct == pytest.approx(0.0, abs=1e-6)
        assert metrics.correlation == pytest.approx(1.0, abs=1e-8)
        assert metrics.r_squared == pytest.approx(1.0, abs=1e-8)
        assert metrics.excess_return_pct == pytest.approx(0.0, abs=1e-8)

    def test_flat_benchmark_yields_none_beta(self):
        idx = pd.date_range("2022-01-01", periods=20, freq="D")
        strategy = pd.Series(1000.0 * (1.01 ** np.arange(20)), index=idx)
        benchmark = pd.Series(1000.0, index=idx)
        metrics = relative_metrics(strategy, benchmark, "flat")
        assert metrics.beta is None
        assert metrics.alpha_annual_pct is None

    def test_quantstats_sharpe_cross_check(self):
        """Cross-checks our annualised Sharpe against quantstats' own
        computation on the same return series, per the module docstring's
        promise that `tests/test_benchmark.py` keeps the in-house math
        honest against a well-known reporting library."""
        quantstats = pytest.importorskip("quantstats")

        idx = pd.date_range("2022-01-03", periods=252, freq="B")
        rng = np.random.default_rng(42)
        curve = pd.Series(
            1000.0 * np.cumprod(1 + rng.normal(0.0006, 0.012, 252)), index=idx
        )

        summary = summarize_curve(curve, "Strategy")
        returns = curve.pct_change().dropna()
        qs_sharpe = quantstats.stats.sharpe(returns, rf=0.0, periods=252)

        assert summary.sharpe_ratio == pytest.approx(qs_sharpe, rel=1e-6)


class TestRunComparison:
    @pytest.fixture
    def frames(self):
        return {"AAPL": _trending_ohlcv(seed=1)}

    @pytest.fixture
    def spy_frame(self):
        return _trending_ohlcv(seed=2, drift=0.15)

    def test_empty_frames_raises(self):
        with pytest.raises(ValueError, match="at least one ticker"):
            run_comparison(MovingAverageCross(), {})

    def test_all_curves_start_at_initial_capital(self, frames, spy_frame):
        comparison = run_comparison(
            MovingAverageCross(), frames, spy_frame=spy_frame, initial_capital=1000.0
        )
        for key in (STRATEGY_KEY, BUY_HOLD_KEY, SPY_KEY):
            assert comparison.curves[key].iloc[0] == pytest.approx(1000.0)

    def test_headline_mentions_dollar_baseline_and_spy(self, frames, spy_frame):
        comparison = run_comparison(
            MovingAverageCross(), frames, spy_frame=spy_frame, initial_capital=1000.0
        )
        headline = comparison.headline()
        assert "$1,000 grown to" in headline
        assert "SPY" in headline

    def test_missing_spy_adds_warning_and_omits_vs_spy(self, frames):
        comparison = run_comparison(MovingAverageCross(), frames, spy_frame=None)
        assert comparison.vs_spy is None
        assert any("SPY" in w for w in comparison.warnings)

    def test_multi_ticker_sleeves_split_capital_and_sum(self, spy_frame):
        frames = {
            "AAPL": _trending_ohlcv(seed=1),
            "MSFT": _trending_ohlcv(seed=3, drift=0.2),
        }
        comparison = run_comparison(
            MovingAverageCross(), frames, spy_frame=spy_frame, initial_capital=1000.0
        )
        assert comparison.tickers == ["AAPL", "MSFT"]
        assert comparison.curves[STRATEGY_KEY].iloc[0] == pytest.approx(1000.0)

    def test_payload_is_strict_json_serializable(self, frames, spy_frame):
        """No NaN/Infinity may reach the API boundary - see the module
        docstring on why `_finite()` maps them to `None`. `json.dumps` with
        `allow_nan=False` raises on a `float('nan')`/`inf` that slipped
        through, which is exactly what a bare NaN would do in the browser's
        `JSON.parse`."""
        comparison = run_comparison(
            MovingAverageCross(), frames, spy_frame=spy_frame, initial_capital=1000.0
        )
        payload = comparison_to_payload(comparison)
        serialized = json.dumps(payload, allow_nan=False)
        assert json.loads(serialized)["initial_capital"] == 1000.0

    def test_curve_summaries_have_no_nan_dollar_values(self, frames, spy_frame):
        comparison = run_comparison(
            MovingAverageCross(), frames, spy_frame=spy_frame, initial_capital=1000.0
        )
        for record in comparison.curve_records():
            for key, value in record.items():
                if key == "date":
                    continue
                assert value is None or math.isfinite(value)
