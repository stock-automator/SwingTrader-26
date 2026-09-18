"""Tests for the Supertrend + Parabolic SAR confluence strategy."""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.strategies.base import BaseStrategy
from backend.app.quant.strategies.supertrend_psar import (
    SupertrendPSARStrategy,
    parabolic_sar,
    supertrend,
)


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """A downtrend that reverses hard into an uptrend, so both Supertrend
    and PSAR flip bullish."""
    rng = np.random.default_rng(6)
    index = pd.date_range("2023-01-02", periods=150, freq="B")

    down = 150 - np.arange(60) * 0.6
    up = down[-1] + np.arange(90) * 0.7
    close = pd.Series(np.concatenate([down, up]) + rng.normal(0, 0.2, 150), index=index)

    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": pd.Series(1_000_000.0, index=index),
        },
        index=index,
    )


class TestSupertrendIndicator:
    def test_rejects_invalid_params(self, sample_data):
        with pytest.raises(ValueError):
            supertrend(sample_data, period=1)
        with pytest.raises(ValueError):
            supertrend(sample_data, multiplier=0)

    def test_direction_is_plus_or_minus_one_after_warmup(self, sample_data):
        result = supertrend(sample_data, period=10)
        warmed_up = result["supertrend_direction"].iloc[15:]
        assert set(warmed_up.unique()) <= {1, -1}

    def test_flips_direction_on_a_hard_reversal(self, sample_data):
        result = supertrend(sample_data, period=10)
        assert result["supertrend_direction"].nunique() >= 2


class TestParabolicSAR:
    def test_rejects_invalid_params(self, sample_data):
        with pytest.raises(ValueError):
            parabolic_sar(sample_data, af_start=0)
        with pytest.raises(ValueError):
            parabolic_sar(sample_data, af_start=0.5, af_max=0.2)

    def test_rejects_too_short_frame(self):
        df = pd.DataFrame({"High": [1.0], "Low": [0.5]})
        with pytest.raises(ValueError):
            parabolic_sar(df)

    def test_stays_within_a_reasonable_band_of_price(self, sample_data):
        sar = parabolic_sar(sample_data)
        # SAR should never be wildly detached from the traded price range.
        assert sar.min() > sample_data["Low"].min() - 50
        assert sar.max() < sample_data["High"].max() + 50


class TestSupertrendPSARStrategy:
    def test_initialization_defaults(self):
        strategy = SupertrendPSARStrategy()
        assert strategy.name == "Supertrend + Parabolic SAR Confluence"

    def test_calculate_indicators(self, sample_data):
        strategy = SupertrendPSARStrategy()
        df = strategy._calculate_indicators(sample_data.copy())
        for col in ("supertrend", "supertrend_direction", "psar", "atr"):
            assert col in df.columns

    def test_generate_signals_schema(self, sample_data):
        strategy = SupertrendPSARStrategy()
        result = strategy.generate_signals(sample_data.copy())

        assert len(result) == len(sample_data)
        BaseStrategy.validate_output(result)
        assert set(result["signal"].unique()) <= {1, 0}

    def test_fires_on_a_hard_reversal(self, sample_data):
        strategy = SupertrendPSARStrategy()
        result = strategy.generate_signals(sample_data.copy())
        assert (result["signal"] == 1).sum() >= 1

    def test_signal_properties(self, sample_data):
        strategy = SupertrendPSARStrategy()
        result = strategy.generate_signals(sample_data.copy())
        active = result[result["signal"] != 0]

        if len(active) > 0:
            assert (active["sl_type"] == "ATR").all()
            assert (active["tp_type"] == "ATR").all()

        inactive = result[result["signal"] == 0]
        assert inactive["sl_value"].isna().all()
