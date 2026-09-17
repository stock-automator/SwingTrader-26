"""
Tests for the Moving Average Cross reference strategy.

This file is the template referenced by AGENTS.md for testing a new
BaseStrategy subclass - copy its structure when adding a strategy.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategies.base_strategy import BaseStrategy
from src.strategies.moving_average_cross import MovingAverageCross


@pytest.fixture
def sample_data():
    """Synthetic OHLCV data with a clear trend change (down -> up -> down)."""

    np.random.seed(7)
    n = 200
    dates = pd.date_range(start="2024-01-01", periods=n, freq="D")

    trend = np.concatenate(
        [
            np.linspace(100, 80, n // 3),
            np.linspace(80, 130, n // 3),
            np.linspace(130, 110, n - 2 * (n // 3)),
        ]
    )
    noise = np.random.randn(n) * 0.3
    close = trend + noise

    df = pd.DataFrame(
        {
            "Open": close - np.abs(np.random.randn(n) * 0.1),
            "High": close + np.abs(np.random.randn(n) * 0.3),
            "Low": close - np.abs(np.random.randn(n) * 0.3),
            "Close": close,
            "Volume": np.random.randint(1_000_000, 5_000_000, n),
        },
        index=dates,
    )

    return df


class TestMovingAverageCross:
    def test_initialization(self):
        strategy = MovingAverageCross()
        assert strategy.fast_period == 20
        assert strategy.slow_period == 50

    def test_rejects_invalid_periods(self):
        with pytest.raises(ValueError):
            MovingAverageCross(fast_period=50, slow_period=20)

    def test_generate_signals_schema(self, sample_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        result = strategy.generate_signals(sample_data.copy())

        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(sample_data)

        BaseStrategy.validate_output(result)

        assert set(result["signal"].unique()) <= {1, -1, 0}

    def test_produces_both_cross_directions(self, sample_data):
        """With a down -> up -> down trend we expect at least one of each cross."""

        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        result = strategy.generate_signals(sample_data.copy())

        assert (result["signal"] == 1).sum() >= 1
        assert (result["signal"] == -1).sum() >= 1

    def test_signal_properties(self, sample_data):
        strategy = MovingAverageCross(
            fast_period=5, slow_period=15, sl_pct=0.03, tp_atr_multiplier=2.5
        )
        result = strategy.generate_signals(sample_data.copy())

        active = result[result["signal"] != 0]
        assert (active["sl_type"] == "PERCENTAGE").all()
        assert (active["tp_type"] == "ATR").all()
        assert (active["sl_value"] == 0.03).all()
        assert (active["tp_value"] == 2.5).all()

        inactive = result[result["signal"] == 0]
        assert inactive["sl_value"].isna().all()
        assert inactive["tp_value"].isna().all()

    def test_no_signal_during_warmup(self, sample_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        result = strategy.generate_signals(sample_data.copy())

        warmup = result.iloc[: strategy.slow_period]
        assert (warmup["signal"] == 0).all()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
