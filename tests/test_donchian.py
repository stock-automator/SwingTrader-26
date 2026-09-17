"""
Tests for Donchian Breakout Strategy
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategies.base_strategy import BaseStrategy
from src.strategies.donchian_breakout import DonchianBreakout


@pytest.fixture
def sample_data():
    """Create sample OHLCV data for testing"""

    dates = pd.date_range(start="2024-01-01", periods=200, freq="D")

    # Create synthetic uptrend with breakouts
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(200) * 0.5) + np.arange(200) * 0.1
    high = close + np.abs(np.random.randn(200) * 0.3)
    low = close - np.abs(np.random.randn(200) * 0.3)
    volume = np.random.randint(1000000, 5000000, 200)

    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": close - np.random.randn(200) * 0.2,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        }
    )

    df.set_index("Date", inplace=True)
    df["ticker"] = "TEST"

    return df


class TestDonchianBreakout:
    """Test suite for DonchianBreakout strategy"""

    def test_initialization(self):
        """Test strategy initialization"""

        strategy = DonchianBreakout()

        assert strategy.name == "Donchian 20-day Breakout"
        assert strategy.breakout_period == 20
        assert strategy.ema_period == 50
        assert strategy.momentum_period == 63

    def test_custom_parameters(self):
        """Test strategy with custom parameters"""

        strategy = DonchianBreakout(
            breakout_period=10, ema_period=30, momentum_period=30
        )

        assert strategy.breakout_period == 10
        assert strategy.ema_period == 30
        assert strategy.momentum_period == 30

    def test_calculate_indicators(self, sample_data):
        """Test indicator calculation"""

        strategy = DonchianBreakout()
        df = strategy._calculate_indicators(sample_data.copy())

        # Check that required columns exist
        assert "donchian_high" in df.columns
        assert "donchian_low" in df.columns
        assert "ema_50" in df.columns
        assert "atr" in df.columns
        assert "volume_spike" in df.columns

        # Check for NaN values (should have some due to lookback)
        assert df["donchian_high"].notna().sum() > 0
        assert df["atr"].notna().sum() > 0

    def test_generate_signals_schema(self, sample_data):
        """generate_signals must return the standard BaseStrategy schema"""

        strategy = DonchianBreakout()
        result = strategy.generate_signals(sample_data.copy())

        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(sample_data)
        assert result.index.equals(sample_data.index)

        # Should not raise
        BaseStrategy.validate_output(result)

        assert set(result["signal"].unique()) <= {1, -1, 0}
        # This strategy only ever emits BUY signals
        assert set(result["signal"].unique()) <= {1, 0}

    def test_signal_properties(self, sample_data):
        """Test SL/TP properties are correct on active signal rows"""

        strategy = DonchianBreakout()
        result = strategy.generate_signals(sample_data.copy())

        active = result[result["signal"] != 0]

        if len(active) > 0:
            assert (active["sl_type"] == "ATR").all()
            assert (active["tp_type"] == "ATR").all()
            assert (active["sl_value"] == strategy.sl_atr_multiplier).all()
            assert (active["tp_value"] == strategy.tp_atr_multiplier).all()

        # Inactive rows carry no SL/TP value
        inactive = result[result["signal"] == 0]
        assert inactive["sl_value"].isna().all()
        assert inactive["tp_value"].isna().all()

    def test_no_signals_in_downtrend(self):
        """Test that strategy generates fewer signals in downtrend"""

        # Create downtrend data
        dates = pd.date_range(start="2024-01-01", periods=100, freq="D")
        close = 100 - np.arange(100) * 0.5  # Downtrend

        df = pd.DataFrame(
            {
                "Open": close,
                "High": close + np.abs(np.random.randn(100) * 0.3),
                "Low": close - np.abs(np.random.randn(100) * 0.3),
                "Close": close,
                "Volume": np.random.randint(1000000, 5000000, 100),
            },
            index=dates,
        )

        df["ticker"] = "TEST"

        strategy = DonchianBreakout()
        result = strategy.generate_signals(df.copy())

        # In downtrend, should have very few (ideally 0) buy signals
        assert (result["signal"] == 1).sum() < 5


class TestSignalValidation:
    """Test the shared BaseStrategy.validate_output contract"""

    def test_rejects_missing_columns(self):
        df = pd.DataFrame({"signal": [1, 0]})
        with pytest.raises(ValueError):
            BaseStrategy.validate_output(df)

    def test_rejects_invalid_signal_value(self):
        df = pd.DataFrame(
            {
                "signal": [2],
                "sl_type": ["ATR"],
                "sl_value": [1.5],
                "tp_type": ["ATR"],
                "tp_value": [3.5],
            }
        )
        with pytest.raises(ValueError):
            BaseStrategy.validate_output(df)

    def test_rejects_invalid_level_type(self):
        df = pd.DataFrame(
            {
                "signal": [1],
                "sl_type": ["BOGUS"],
                "sl_value": [1.5],
                "tp_type": ["ATR"],
                "tp_value": [3.5],
            }
        )
        with pytest.raises(ValueError):
            BaseStrategy.validate_output(df)

    def test_rejects_non_positive_value(self):
        df = pd.DataFrame(
            {
                "signal": [1],
                "sl_type": ["ATR"],
                "sl_value": [0.0],
                "tp_type": ["ATR"],
                "tp_value": [3.5],
            }
        )
        with pytest.raises(ValueError):
            BaseStrategy.validate_output(df)

    def test_accepts_valid_output(self):
        df = pd.DataFrame(
            {
                "signal": [1, 0, -1],
                "sl_type": ["ATR", None, "PERCENTAGE"],
                "sl_value": [1.5, np.nan, 0.02],
                "tp_type": ["ATR", None, "PERCENTAGE"],
                "tp_value": [3.5, np.nan, 0.04],
            }
        )
        BaseStrategy.validate_output(df)  # should not raise


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
