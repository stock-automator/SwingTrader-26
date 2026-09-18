"""Tests for the Bollinger-Keltner Squeeze Breakout strategy."""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.strategies.base import BaseStrategy
from backend.app.quant.strategies.bollinger_keltner_squeeze import (
    BollingerKeltnerSqueezeStrategy,
)


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """A quiet consolidation followed by a sharp breakout - engineered so a
    squeeze forms and then releases upward."""
    rng = np.random.default_rng(11)
    index = pd.date_range("2023-01-02", periods=150, freq="B")

    quiet = 100 + rng.normal(0, 0.15, 100).cumsum() * 0.1
    breakout = quiet[-1] + np.cumsum(np.abs(rng.normal(1.0, 0.3, 50)))
    close = pd.Series(np.concatenate([quiet, breakout]), index=index)

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


class TestBollingerKeltnerSqueezeStrategy:
    def test_initialization_defaults(self):
        strategy = BollingerKeltnerSqueezeStrategy()
        assert strategy.name == "Bollinger-Keltner Squeeze Breakout"
        assert strategy.bb_period == 20
        assert strategy.kc_period == 20

    def test_rejects_invalid_params(self):
        with pytest.raises(ValueError):
            BollingerKeltnerSqueezeStrategy(bb_period=1)
        with pytest.raises(ValueError):
            BollingerKeltnerSqueezeStrategy(bb_stddev=0)
        with pytest.raises(ValueError):
            BollingerKeltnerSqueezeStrategy(min_squeeze_bars=0)

    def test_calculate_indicators(self, sample_data):
        strategy = BollingerKeltnerSqueezeStrategy()
        df = strategy._calculate_indicators(sample_data.copy())

        for col in (
            "bb_upper",
            "bb_lower",
            "kc_upper",
            "kc_lower",
            "squeeze_on",
            "atr",
        ):
            assert col in df.columns
        assert df["squeeze_on"].dtype == bool
        assert df["bb_upper"].notna().sum() > 0

    def test_generate_signals_schema(self, sample_data):
        strategy = BollingerKeltnerSqueezeStrategy()
        result = strategy.generate_signals(sample_data.copy())

        assert len(result) == len(sample_data)
        assert result.index.equals(sample_data.index)
        BaseStrategy.validate_output(result)
        assert set(result["signal"].unique()) <= {1, 0}

    def test_detects_squeeze_release_breakout(self, sample_data):
        strategy = BollingerKeltnerSqueezeStrategy(min_squeeze_bars=5)
        result = strategy.generate_signals(sample_data.copy())

        # The fixture is engineered specifically to produce a squeeze then a
        # sharp breakout - at least one signal should fire during it.
        assert (result["signal"] == 1).sum() >= 1

    def test_signal_properties(self, sample_data):
        strategy = BollingerKeltnerSqueezeStrategy()
        result = strategy.generate_signals(sample_data.copy())
        active = result[result["signal"] != 0]

        if len(active) > 0:
            assert (active["sl_type"] == "ATR").all()
            assert (active["tp_type"] == "ATR").all()
            assert (active["sl_value"] == strategy.sl_atr_multiplier).all()
            assert (active["tp_value"] == strategy.tp_atr_multiplier).all()

        inactive = result[result["signal"] == 0]
        assert inactive["sl_value"].isna().all()
        assert inactive["tp_value"].isna().all()
