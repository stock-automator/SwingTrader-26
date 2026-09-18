"""Tests for the KAMA Dynamic Trend strategy."""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.strategies.base import BaseStrategy
from backend.app.quant.strategies.kama_trend import (
    KAMATrendStrategy,
    kama,
    kaufman_efficiency_ratio,
)


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """A pullback-then-resumption series, so price crosses back above its
    own KAMA at least once."""
    rng = np.random.default_rng(5)
    index = pd.date_range("2023-01-02", periods=150, freq="B")

    up1 = 100 + np.arange(50) * 0.4
    pullback = up1[-1] - np.arange(20) * 0.5
    up2 = pullback[-1] + np.arange(80) * 0.5
    close = pd.Series(
        np.concatenate([up1, pullback, up2]) + rng.normal(0, 0.1, 150), index=index
    )

    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close + 0.3,
            "Low": close - 0.3,
            "Close": close,
            "Volume": pd.Series(1_000_000.0, index=index),
        },
        index=index,
    )


class TestKamaHelpers:
    def test_efficiency_ratio_is_one_on_a_straight_line(self):
        close = pd.Series(np.arange(30, dtype=float) + 100.0)
        er = kaufman_efficiency_ratio(close, period=10)
        assert np.isclose(er.iloc[-1], 1.0)

    def test_efficiency_ratio_is_low_on_pure_noise(self):
        rng = np.random.default_rng(1)
        close = pd.Series(100.0 + rng.normal(0, 1, 200).cumsum() * 0)  # flat + noise
        close = pd.Series(100.0 + rng.normal(0, 1, 200))
        er = kaufman_efficiency_ratio(close, period=10)
        assert er.iloc[-1] < 0.6

    def test_kama_rejects_empty_series(self):
        with pytest.raises(ValueError):
            kama(pd.Series(dtype=float))

    def test_kama_tracks_close_reasonably(self):
        close = pd.Series(np.arange(60, dtype=float) + 100.0)
        result = kama(close, er_period=10)
        tail = result.dropna()
        assert len(tail) > 0
        # On a straight line, efficiency ratio is 1.0 -> fast tracking, so
        # KAMA should sit close to price by the end.
        assert abs(tail.iloc[-1] - close.iloc[-1]) < 2.0


class TestKAMATrendStrategy:
    def test_initialization_defaults(self):
        strategy = KAMATrendStrategy()
        assert strategy.name == "KAMA Dynamic Trend"
        assert strategy.er_period == 10

    def test_rejects_invalid_params(self):
        with pytest.raises(ValueError):
            KAMATrendStrategy(slow_period=2, fast_period=5)
        with pytest.raises(ValueError):
            KAMATrendStrategy(rising_lookback=0)

    def test_calculate_indicators(self, sample_data):
        strategy = KAMATrendStrategy()
        df = strategy._calculate_indicators(sample_data.copy())
        assert "kama" in df.columns
        assert "atr" in df.columns
        assert df["kama"].notna().sum() > 0

    def test_generate_signals_schema(self, sample_data):
        strategy = KAMATrendStrategy()
        result = strategy.generate_signals(sample_data.copy())

        assert len(result) == len(sample_data)
        assert result.index.equals(sample_data.index)
        BaseStrategy.validate_output(result)
        assert set(result["signal"].unique()) <= {1, 0}

    def test_signal_properties(self, sample_data):
        strategy = KAMATrendStrategy()
        result = strategy.generate_signals(sample_data.copy())
        active = result[result["signal"] != 0]

        if len(active) > 0:
            assert (active["sl_type"] == "ATR").all()
            assert (active["tp_type"] == "ATR").all()

        inactive = result[result["signal"] == 0]
        assert inactive["sl_value"].isna().all()
        assert inactive["tp_value"].isna().all()
