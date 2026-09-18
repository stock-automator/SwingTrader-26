"""Tests for the OBV Bullish Divergence strategy."""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.strategies.base import BaseStrategy
from backend.app.quant.strategies.obv_divergence import (
    OBVDivergenceStrategy,
    on_balance_volume,
)


@pytest.fixture
def divergence_data() -> pd.DataFrame:
    """Price grinds to a lower low on shrinking volume (net selling
    pressure drying up, i.e. OBV makes a higher low), then turns up hard on
    strong volume - a textbook bullish divergence + confirmation."""
    index = pd.date_range("2023-01-02", periods=80, freq="B")

    leg1_close = 100 - np.arange(20) * 0.5  # first low ~90.5, heavy volume
    leg1_volume = np.full(20, 2_000_000.0)

    bounce_close = leg1_close[-1] + np.arange(15) * 0.3
    bounce_volume = np.full(15, 1_000_000.0)

    leg2_close = bounce_close[-1] - np.arange(20) * 0.55  # lower low, light volume
    leg2_volume = np.full(20, 500_000.0)

    turn_close = leg2_close[-1] + np.cumsum(
        np.full(25, 0.8)
    )  # sharp reversal, volume surges
    turn_volume = np.linspace(1_500_000.0, 3_000_000.0, 25)

    close = pd.Series(
        np.concatenate([leg1_close, bounce_close, leg2_close, turn_close]), index=index
    )
    volume = pd.Series(
        np.concatenate([leg1_volume, bounce_volume, leg2_volume, turn_volume]),
        index=index,
    )

    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close + 0.4,
            "Low": close - 0.4,
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )


class TestOnBalanceVolume:
    def test_rejects_empty_frame(self):
        with pytest.raises(ValueError):
            on_balance_volume(pd.DataFrame())

    def test_rejects_missing_columns(self):
        with pytest.raises(ValueError):
            on_balance_volume(pd.DataFrame({"Close": [1.0, 2.0]}))

    def test_up_day_adds_volume_down_day_subtracts(self):
        df = pd.DataFrame({"Close": [10.0, 11.0, 10.5], "Volume": [100.0, 200.0, 50.0]})
        obv = on_balance_volume(df)
        assert obv.iloc[0] == 0.0
        assert obv.iloc[1] == 200.0
        assert obv.iloc[2] == 150.0


class TestOBVDivergenceStrategy:
    def test_initialization_defaults(self):
        strategy = OBVDivergenceStrategy()
        assert strategy.name == "OBV Bullish Divergence"

    def test_rejects_invalid_params(self):
        with pytest.raises(ValueError):
            OBVDivergenceStrategy(divergence_lookback=1)
        with pytest.raises(ValueError):
            OBVDivergenceStrategy(low_lookback=0)

    def test_generate_signals_schema(self, divergence_data):
        strategy = OBVDivergenceStrategy(divergence_lookback=15, low_lookback=3)
        result = strategy.generate_signals(divergence_data.copy())

        assert len(result) == len(divergence_data)
        BaseStrategy.validate_output(result)
        assert set(result["signal"].unique()) <= {1, 0}

    def test_detects_bullish_divergence_and_turn(self, divergence_data):
        strategy = OBVDivergenceStrategy(divergence_lookback=15, low_lookback=3)
        result = strategy.generate_signals(divergence_data.copy())
        assert (result["signal"] == 1).sum() >= 1

    def test_signal_properties(self, divergence_data):
        strategy = OBVDivergenceStrategy(divergence_lookback=15, low_lookback=3)
        result = strategy.generate_signals(divergence_data.copy())
        active = result[result["signal"] != 0]

        if len(active) > 0:
            assert (active["sl_type"] == "ATR").all()
            assert (active["tp_type"] == "ATR").all()

        inactive = result[result["signal"] == 0]
        assert inactive["sl_value"].isna().all()
