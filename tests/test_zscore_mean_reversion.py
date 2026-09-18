"""Tests for the Z-Score Mean Reversion (Hurst-filtered) strategy."""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.strategies.base import BaseStrategy
from backend.app.quant.strategies.zscore_mean_reversion import (
    ZScoreMeanReversionStrategy,
    hurst_exponent,
    rolling_hurst,
)


def _ou_process(n: int, theta: float, mu: float, sigma: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    x[0] = mu
    for t in range(1, n):
        x[t] = x[t - 1] + theta * (mu - x[t - 1]) + rng.normal(0, sigma)
    return x


@pytest.fixture
def mean_reverting_data() -> pd.DataFrame:
    index = pd.date_range("2023-01-02", periods=400, freq="B")
    close = pd.Series(
        _ou_process(400, theta=0.08, mu=100.0, sigma=1.5, seed=3), index=index
    )
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": pd.Series(1_000_000.0, index=index),
        },
        index=index,
    )


@pytest.fixture
def trending_data() -> pd.DataFrame:
    rng = np.random.default_rng(9)
    index = pd.date_range("2023-01-02", periods=400, freq="B")
    close = pd.Series(
        100.0 + np.arange(400) * 0.5 + rng.normal(0, 0.3, 400), index=index
    )
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": pd.Series(1_000_000.0, index=index),
        },
        index=index,
    )


class TestHurstExponent:
    def test_mean_reverting_series_scores_below_half(self):
        series = _ou_process(1000, theta=0.1, mu=100.0, sigma=1.0, seed=1)
        assert hurst_exponent(series) < 0.5

    def test_stationary_white_noise_scores_near_zero(self):
        """Undifferentiated iid noise has no memory at any lag - std(diff)
        is flat across lags, so the regression slope (the Hurst estimate)
        should sit near 0, the strongest possible mean-reversion read."""
        rng = np.random.default_rng(4)
        series = rng.normal(0, 1, 1000)
        assert hurst_exponent(series) < 0.3

    def test_random_walk_is_not_classified_as_mean_reverting(self):
        """A pure random walk (H=0.5 in theory) should score clearly above
        the OU/white-noise cases above, even if estimation noise keeps it
        from landing exactly on 0.5."""
        rng = np.random.default_rng(2)
        series = np.cumsum(rng.normal(0, 1, 2000))
        assert hurst_exponent(series) > 0.35

    def test_short_series_returns_neutral_default(self):
        assert hurst_exponent(np.array([1.0, 2.0, 3.0])) == 0.5

    def test_rolling_hurst_rejects_window_not_exceeding_max_lag(self):
        close = pd.Series(np.arange(50, dtype=float))
        with pytest.raises(ValueError):
            rolling_hurst(close, window=10)


class TestZScoreMeanReversionStrategy:
    def test_initialization_defaults(self):
        strategy = ZScoreMeanReversionStrategy()
        assert strategy.name == "Z-Score Mean Reversion (Hurst-Filtered)"
        assert strategy.zscore_period == 20

    def test_rejects_invalid_params(self):
        with pytest.raises(ValueError):
            ZScoreMeanReversionStrategy(zscore_period=1)
        with pytest.raises(ValueError):
            ZScoreMeanReversionStrategy(entry_zscore=0)
        with pytest.raises(ValueError):
            ZScoreMeanReversionStrategy(hurst_threshold=1.5)

    def test_generate_signals_schema(self, mean_reverting_data):
        strategy = ZScoreMeanReversionStrategy()
        result = strategy.generate_signals(mean_reverting_data.copy())

        assert len(result) == len(mean_reverting_data)
        BaseStrategy.validate_output(result)
        assert set(result["signal"].unique()) <= {1, 0}

    def test_fires_on_mean_reverting_regime(self, mean_reverting_data):
        strategy = ZScoreMeanReversionStrategy()
        result = strategy.generate_signals(mean_reverting_data.copy())
        assert (result["signal"] == 1).sum() > 0

    def test_suppressed_in_a_strong_trend(self, trending_data):
        """A steady uptrend never pulls the rolling z-score down to the
        oversold entry threshold, so no signal should fire regardless of
        the Hurst regime read."""
        strategy = ZScoreMeanReversionStrategy()
        result = strategy.generate_signals(trending_data.copy())
        assert (result["signal"] == 1).sum() == 0

    def test_signal_properties(self, mean_reverting_data):
        strategy = ZScoreMeanReversionStrategy()
        result = strategy.generate_signals(mean_reverting_data.copy())
        active = result[result["signal"] != 0]

        if len(active) > 0:
            assert (active["sl_type"] == "ATR").all()
            assert (active["tp_type"] == "ATR").all()

        inactive = result[result["signal"] == 0]
        assert inactive["sl_value"].isna().all()
