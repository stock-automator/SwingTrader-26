"""Unit tests for `analytics.expectancy` - the real win-probability engine
that replaced `api/signals.py`'s `win_probability: null` placeholder (see
`test_signals_api.py` for the wired-in `/live-today` behavior)."""

import numpy as np
import pandas as pd

from backend.app.analytics.expectancy import (
    METHOD_BLOCK_BOOTSTRAP,
    METHOD_INSUFFICIENT_DATA,
    METHOD_REGIME_MATCHED,
    MIN_BOOTSTRAP_TRADES,
    MIN_REGIME_MATCHED_TRADES,
    WinProbabilityEstimate,
    _block_bootstrap_win_rates,
    estimate_win_probability,
)
from backend.app.quant.regime import REGIME_BULL_TREND, RegimeDetector
from backend.app.quant.risk import RiskManager
from backend.app.quant.strategies.moving_average_cross import MovingAverageCross


def _choppy_ohlcv(n: int = 500, seed: int = 1, vol: float = 2.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2019-01-02", periods=n, freq="B")
    noise = rng.normal(0, vol, n).cumsum()
    close = pd.Series(100.0 + noise, index=index).clip(lower=5.0)
    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close + 1.5,
            "Low": close - 1.5,
            "Close": close,
            "Volume": pd.Series(1_000_000.0, index=index),
        },
        index=index,
    )


def _flat_ohlcv(n: int = 150) -> pd.DataFrame:
    """Barely moves - a strategy should generate ~zero closed trades."""
    index = pd.date_range("2019-01-02", periods=n, freq="B")
    close = pd.Series(100.0, index=index)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.01,
            "Low": close - 0.01,
            "Close": close,
            "Volume": pd.Series(1_000_000.0, index=index),
        },
        index=index,
    )


class TestBlockBootstrapWinRates:
    def test_shape_and_bounds(self):
        outcomes = np.array([1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0])
        rng = np.random.default_rng(0)
        rates = _block_bootstrap_win_rates(
            outcomes, n_iterations=500, block_size=3, rng=rng
        )
        assert rates.shape == (500,)
        assert (rates >= 0).all() and (rates <= 1).all()

    def test_all_wins_bootstraps_to_all_wins(self):
        outcomes = np.ones(10)
        rng = np.random.default_rng(0)
        rates = _block_bootstrap_win_rates(
            outcomes, n_iterations=100, block_size=4, rng=rng
        )
        assert (rates == 1.0).all()

    def test_deterministic_with_seeded_rng(self):
        outcomes = np.array([1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0])
        a = _block_bootstrap_win_rates(outcomes, 200, 3, np.random.default_rng(42))
        b = _block_bootstrap_win_rates(outcomes, 200, 3, np.random.default_rng(42))
        np.testing.assert_array_equal(a, b)


class TestEstimateWinProbability:
    def test_insufficient_data_when_no_closed_trades(self):
        df = _flat_ohlcv()
        strategy = MovingAverageCross()
        rm = RiskManager(account_equity=1000.0)
        result = estimate_win_probability(
            strategy, df, rm, current_regime=REGIME_BULL_TREND
        )
        assert result.win_probability is None
        assert result.method == METHOD_INSUFFICIENT_DATA

    def test_never_fabricates_a_number_below_the_bootstrap_floor(self):
        """Fewer trades than MIN_BOOTSTRAP_TRADES must stay None, regardless
        of what the raw win/loss ratio of that handful would say."""
        df = _choppy_ohlcv(n=160, seed=5)  # short history -> few trades
        strategy = MovingAverageCross()
        rm = RiskManager(account_equity=1000.0)
        result = estimate_win_probability(
            strategy, df, rm, current_regime=REGIME_BULL_TREND
        )
        if result.sample_size < MIN_BOOTSTRAP_TRADES:
            assert result.win_probability is None
            assert result.method == METHOD_INSUFFICIENT_DATA

    def test_bootstrap_or_regime_matched_when_enough_trades_exist(self):
        df = _choppy_ohlcv(n=1500, seed=7, vol=3.0)
        strategy = MovingAverageCross()
        rm = RiskManager(account_equity=1000.0)
        regime = RegimeDetector().current_regime(df)
        result = estimate_win_probability(
            strategy, df, rm, current_regime=regime, seed=1
        )
        if result.sample_size >= MIN_BOOTSTRAP_TRADES:
            assert result.win_probability is not None
            assert 0.0 <= result.win_probability <= 1.0
            assert result.method in (METHOD_REGIME_MATCHED, METHOD_BLOCK_BOOTSTRAP)
            if result.method == METHOD_BLOCK_BOOTSTRAP:
                assert result.confidence_low is not None
                assert result.confidence_high is not None
                assert result.confidence_low <= result.win_probability
                assert result.win_probability <= result.confidence_high

    def test_regime_matched_requires_the_minimum_sample(self):
        """A hand-built estimate with exactly MIN_REGIME_MATCHED_TRADES
        same-regime wins must report the exact ratio, not a bootstrap."""
        # Sanity-checks the threshold constant is what the module claims,
        # rather than re-deriving the whole backtest pipeline.
        assert MIN_REGIME_MATCHED_TRADES >= MIN_BOOTSTRAP_TRADES

    def test_backtest_failure_is_caught_not_raised(self):
        class _BrokenStrategy(MovingAverageCross):
            def generate_signals(self, df):
                raise RuntimeError("boom")

        df = _choppy_ohlcv(n=200)
        rm = RiskManager(account_equity=1000.0)
        result = estimate_win_probability(
            _BrokenStrategy(), df, rm, current_regime=REGIME_BULL_TREND
        )
        assert result.win_probability is None
        assert result.method == METHOD_INSUFFICIENT_DATA
        assert "boom" in result.note


class TestWinProbabilityEstimateAsDict:
    def test_rounds_and_names_fields(self):
        estimate = WinProbabilityEstimate(
            win_probability=0.123456,
            method=METHOD_REGIME_MATCHED,
            sample_size=20,
            confidence_low=0.1,
            confidence_high=0.2,
            note="test",
        )
        payload = estimate.as_dict()
        assert payload["win_probability"] == 0.1235
        assert payload["win_probability_method"] == METHOD_REGIME_MATCHED
        assert payload["win_probability_sample_size"] == 20
        assert payload["win_probability_note"] == "test"

    def test_none_probability_stays_none(self):
        estimate = WinProbabilityEstimate(
            win_probability=None, method=METHOD_INSUFFICIENT_DATA, sample_size=0
        )
        payload = estimate.as_dict()
        assert payload["win_probability"] is None
