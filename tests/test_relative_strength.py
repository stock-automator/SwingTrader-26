"""
Tests for RelativeStrengthStrategy: RS-vs-benchmark leadership + short-EMA
pullback + resumption.
"""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.strategies.base import BaseStrategy
from backend.app.quant.strategies.relative_strength import RelativeStrengthStrategy


def _ohlc(close, high, low, volume, start="2020-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(close), freq="D")
    return pd.DataFrame(
        {"Open": close, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    ).astype(
        {"Open": float, "High": float, "Low": float, "Close": float, "Volume": float}
    )


def _benchmark(n: int = 250, slope: float = 0.03) -> pd.DataFrame:
    close = 100 + slope * np.arange(n, dtype=float)
    return _ohlc(close, close + 0.2, close - 0.2, np.full(n, 1_000_000.0))


def _leader_with_pullback(
    n: int = 250,
    slope: float = 0.3,
    dip_pct: float = 0.90,
    resumption_pct: float = 1.02,
) -> pd.DataFrame:
    """An outperforming ticker that pulls back sharply for 5 bars, then
    resumes with a strong up-close breaking the prior bar's high."""
    close = 100 + slope * np.arange(n, dtype=float)
    dip_start = n - 10
    peak = close[dip_start - 1]

    for i in range(dip_start, n - 1):
        close[i] = peak * dip_pct
    close[n - 1] = peak * resumption_pct

    high = close + 0.3
    low = close - 0.3
    for i in range(dip_start, dip_start + 5):
        low[i] = close[i] - 1.0  # the dip actually pierces below the fast EMA

    volume = np.full(n, 1_000_000.0)
    return _ohlc(close, high, low, volume)


def _laggard_no_pullback(n: int = 250) -> pd.DataFrame:
    """Underperforms the benchmark and never pulls back - a flat, listless
    series drifting below the benchmark's own slope."""
    rng = np.random.default_rng(21)
    close = 100 + 0.005 * np.arange(n, dtype=float) + rng.normal(0, 0.05, n)
    high = close + 0.2
    low = close - 0.2
    volume = np.full(n, 1_000_000.0)
    return _ohlc(close, high, low, volume)


@pytest.fixture
def strategy():
    return RelativeStrengthStrategy()


class TestConstruction:
    def test_rejects_short_rs_lookback(self):
        with pytest.raises(ValueError, match="rs_lookback"):
            RelativeStrengthStrategy(rs_lookback=1)

    def test_rejects_short_trend_sma_period(self):
        with pytest.raises(ValueError, match="trend_sma_period"):
            RelativeStrengthStrategy(trend_sma_period=1)

    def test_rejects_short_pullback_ema_period(self):
        with pytest.raises(ValueError, match="pullback_ema_period"):
            RelativeStrengthStrategy(pullback_ema_period=1)

    def test_rejects_non_positive_pullback_lookback(self):
        with pytest.raises(ValueError, match="pullback_lookback"):
            RelativeStrengthStrategy(pullback_lookback=0)


class TestSetBenchmark:
    def test_rejects_missing_close(self, strategy):
        with pytest.raises(ValueError, match="missing required column"):
            strategy.set_benchmark(pd.DataFrame({"Open": [1.0]}))

    def test_rejects_empty_benchmark(self, strategy):
        with pytest.raises(ValueError, match="empty"):
            strategy.set_benchmark(pd.DataFrame({"Close": []}))

    def test_requires_benchmark_before_generate_signals(self, strategy):
        with pytest.raises(ValueError, match="set_benchmark"):
            strategy.generate_signals(_leader_with_pullback())


class TestGenerateSignals:
    def test_satisfies_base_strategy_output_contract(self, strategy):
        strategy.set_benchmark(_benchmark())
        out = strategy.generate_signals(_leader_with_pullback())
        BaseStrategy.validate_output(out)

    def test_fires_on_outperformance_plus_pullback_resumption(self, strategy):
        strategy.set_benchmark(_benchmark())
        out = strategy.generate_signals(_leader_with_pullback())
        assert out["signal"].iloc[-1] == 1
        assert (out["signal"] == 1).sum() == 1

    def test_reward_risk_ratio_clears_the_risk_engine_minimum(self, strategy):
        strategy.set_benchmark(_benchmark())
        out = strategy.generate_signals(_leader_with_pullback())
        row = out.iloc[-1]
        assert row["tp_value"] / row["sl_value"] >= 2.5

    def test_no_signal_for_a_laggard(self, strategy):
        strategy.set_benchmark(_benchmark())
        out = strategy.generate_signals(_laggard_no_pullback())
        assert (out["signal"] == 1).sum() == 0

    def test_no_signal_without_a_recent_pullback(self, strategy):
        # Same leadership, but no dip at all - straight-line outperformance
        # never touches its own fast EMA, so there is nothing to "resume"
        # from.
        strategy.set_benchmark(_benchmark())
        n = 250
        close = 100 + 0.3 * np.arange(n, dtype=float)
        data = _ohlc(close, close + 0.3, close - 0.3, np.full(n, 1_000_000.0))
        out = strategy.generate_signals(data)
        assert (out["signal"] == 1).sum() == 0

    def test_relative_strength_column_reflects_outperformance(self, strategy):
        strategy.set_benchmark(_benchmark())
        out = strategy.generate_signals(_leader_with_pullback())
        assert out["relative_strength"].iloc[-1] > 0

    def test_no_lookahead_truncation_invariance(self, strategy):
        strategy.set_benchmark(_benchmark())
        data = _leader_with_pullback()
        cut = len(data) - 20

        full = strategy.generate_signals(data)
        truncated = strategy.generate_signals(data.iloc[:cut])

        buffer = 10
        pd.testing.assert_frame_equal(
            full.iloc[: cut - buffer][["signal", "sl_value", "tp_value"]],
            truncated.iloc[: cut - buffer][["signal", "sl_value", "tp_value"]],
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
