"""Tests for the Dual Momentum Engine (relative + absolute momentum)."""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.strategies.base import BaseStrategy
from backend.app.quant.strategies.dual_momentum import DualMomentumStrategy


def _trending_ohlcv(n: int, drift: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2022-01-03", periods=n, freq="B")
    close = pd.Series(
        100.0 + np.arange(n) * drift + rng.normal(0, 1.0, n).cumsum(), index=index
    ).clip(lower=5.0)
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


@pytest.fixture
def leader_and_benchmark():
    leader = _trending_ohlcv(n=300, drift=0.6, seed=1)
    benchmark = _trending_ohlcv(n=300, drift=0.1, seed=2)
    return leader, benchmark


class TestDualMomentumStrategy:
    def test_initialization_defaults(self):
        strategy = DualMomentumStrategy()
        assert strategy.name == "Dual Momentum Engine"
        assert strategy.momentum_lookback == 126

    def test_rejects_invalid_params(self):
        with pytest.raises(ValueError):
            DualMomentumStrategy(momentum_lookback=1)
        with pytest.raises(ValueError):
            DualMomentumStrategy(trend_sma_period=1)

    def test_requires_benchmark_before_generate_signals(self, leader_and_benchmark):
        leader, _ = leader_and_benchmark
        strategy = DualMomentumStrategy()
        with pytest.raises(ValueError, match="set_benchmark"):
            strategy.generate_signals(leader.copy())

    def test_set_benchmark_validates_input(self):
        strategy = DualMomentumStrategy()
        with pytest.raises(ValueError):
            strategy.set_benchmark(pd.DataFrame())
        with pytest.raises(ValueError):
            strategy.set_benchmark(pd.DataFrame({"Open": [1.0]}))

    def test_generate_signals_schema(self, leader_and_benchmark):
        leader, benchmark = leader_and_benchmark
        strategy = DualMomentumStrategy(momentum_lookback=60, trend_sma_period=30)
        strategy.set_benchmark(benchmark)
        result = strategy.generate_signals(leader.copy())

        assert len(result) == len(leader)
        BaseStrategy.validate_output(result)
        assert set(result["signal"].unique()) <= {1, 0}

    def test_leader_outperforming_benchmark_fires_signals(self, leader_and_benchmark):
        leader, benchmark = leader_and_benchmark
        strategy = DualMomentumStrategy(momentum_lookback=60, trend_sma_period=30)
        strategy.set_benchmark(benchmark)
        result = strategy.generate_signals(leader.copy())
        assert (result["signal"] == 1).sum() > 0

    def test_laggard_with_negative_absolute_momentum_does_not_fire(self):
        laggard = _trending_ohlcv(n=300, drift=-0.6, seed=1)
        benchmark = _trending_ohlcv(n=300, drift=0.1, seed=2)
        strategy = DualMomentumStrategy(momentum_lookback=60, trend_sma_period=30)
        strategy.set_benchmark(benchmark)
        result = strategy.generate_signals(laggard.copy())
        assert (result["signal"] == 1).sum() == 0

    def test_signal_properties(self, leader_and_benchmark):
        leader, benchmark = leader_and_benchmark
        strategy = DualMomentumStrategy(momentum_lookback=60, trend_sma_period=30)
        strategy.set_benchmark(benchmark)
        result = strategy.generate_signals(leader.copy())
        active = result[result["signal"] != 0]

        if len(active) > 0:
            assert (active["sl_type"] == "ATR").all()
            assert (active["tp_type"] == "ATR").all()

        inactive = result[result["signal"] == 0]
        assert inactive["sl_value"].isna().all()
