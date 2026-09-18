"""
Tests for VCPBreakoutStrategy: contracting-wave base + volume dry-up +
breakout expansion.
"""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.strategies.base import BaseStrategy
from backend.app.quant.strategies.vcp_breakout import VCPBreakoutStrategy


def _ohlc(close, high, low, volume, start="2020-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(close), freq="D")
    return pd.DataFrame(
        {"Open": close, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    ).astype(
        {"Open": float, "High": float, "Low": float, "Close": float, "Volume": float}
    )


def _vcp_setup_data(
    breakout_multiplier: float = 8.0,
    breakout_volume: float = 2_000_000.0,
    n_pre: int = 160,
    wave_period: int = 15,
) -> pd.DataFrame:
    """A clean textbook VCP: 160 bars of pre-history (uptrend, for weekly EMA
    warm-up), then three contracting/volume-declining waves, then one
    breakout bar."""
    pre_close = np.linspace(50, 100, n_pre)
    pre_high = pre_close + 0.5
    pre_low = pre_close - 0.5
    pre_volume = np.full(n_pre, 1_000_000.0)

    base_price = pre_close[-1]

    def wave(hi_amp: float, lo_amp: float, vol: float):
        return (
            np.full(wave_period, base_price + hi_amp),
            np.full(wave_period, base_price - lo_amp),
            np.full(wave_period, base_price),
            np.full(wave_period, vol),
        )

    w1 = wave(6, 6, 3_000_000.0)
    w2 = wave(4, 4, 2_000_000.0)
    w3 = wave(2, 2, 1_000_000.0)

    breakout_close = base_price + breakout_multiplier
    breakout_high = breakout_close + 0.2
    breakout_low = base_price + 1

    close = np.concatenate([pre_close, w1[2], w2[2], w3[2], [breakout_close]])
    high = np.concatenate([pre_high, w1[0], w2[0], w3[0], [breakout_high]])
    low = np.concatenate([pre_low, w1[1], w2[1], w3[1], [breakout_low]])
    volume = np.concatenate([pre_volume, w1[3], w2[3], w3[3], [breakout_volume]])

    return _ohlc(close, high, low, volume)


def _no_pattern_data(n: int = 250) -> pd.DataFrame:
    """Random noise around a flat mean: no contraction, no breakout."""
    rng = np.random.default_rng(11)
    close = 100 + rng.normal(0, 1.5, n)
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    volume = rng.uniform(800_000, 1_200_000, n)
    return _ohlc(close, high, low, volume)


@pytest.fixture
def strategy():
    return VCPBreakoutStrategy()


class TestConstruction:
    def test_rejects_short_wave_period(self):
        with pytest.raises(ValueError, match="wave_period"):
            VCPBreakoutStrategy(wave_period=1)

    def test_rejects_too_few_contractions(self):
        with pytest.raises(ValueError, match="num_contractions"):
            VCPBreakoutStrategy(num_contractions=1)

    def test_rejects_non_positive_volume_multiple(self):
        with pytest.raises(ValueError, match="breakout_volume_multiple"):
            VCPBreakoutStrategy(breakout_volume_multiple=0)

    def test_derives_base_period(self):
        strategy = VCPBreakoutStrategy(wave_period=10, num_contractions=4)
        assert strategy.base_period == 40


class TestGenerateSignals:
    def test_satisfies_base_strategy_output_contract(self, strategy):
        out = strategy.generate_signals(_vcp_setup_data())
        BaseStrategy.validate_output(out)

    def test_fires_on_a_textbook_contraction_and_breakout(self, strategy):
        out = strategy.generate_signals(_vcp_setup_data())
        assert out["signal"].iloc[-1] == 1
        assert (out["signal"] == 1).sum() == 1

    def test_reward_risk_ratio_clears_the_risk_engine_minimum(self, strategy):
        # tp_atr_multiplier / sl_atr_multiplier = 4.0 / 1.5, by construction.
        out = strategy.generate_signals(_vcp_setup_data())
        row = out.iloc[-1]
        assert row["tp_value"] / row["sl_value"] >= 2.5

    def test_no_breakout_without_a_price_break(self, strategy):
        # Same contracting waves, but the "breakout" bar doesn't actually
        # clear the base high.
        data = _vcp_setup_data(breakout_multiplier=1.0)  # stays inside the base
        out = strategy.generate_signals(data)
        assert (out["signal"] == 1).sum() == 0

    def test_no_signal_without_volume_expansion(self, strategy):
        data = _vcp_setup_data(breakout_volume=1_000_000.0)  # no expansion
        out = strategy.generate_signals(data)
        assert (out["signal"] == 1).sum() == 0

    def test_no_signal_on_pure_noise(self, strategy):
        out = strategy.generate_signals(_no_pattern_data())
        assert (out["signal"] == 1).sum() == 0

    def test_no_signal_when_waves_are_expanding_not_contracting(self, strategy):
        # Reverse the amplitude ordering: waves get *wider*, not tighter.
        data = _vcp_setup_data()
        # Rebuild with expanding waves by swapping wave1/wave3 amplitudes.
        n_pre = 160
        wave_period = 15
        pre_close = np.linspace(50, 100, n_pre)
        base_price = pre_close[-1]

        def wave(hi_amp, lo_amp, vol):
            return (
                np.full(wave_period, base_price + hi_amp),
                np.full(wave_period, base_price - lo_amp),
                np.full(wave_period, base_price),
                np.full(wave_period, vol),
            )

        w1 = wave(2, 2, 1_000_000.0)
        w2 = wave(4, 4, 2_000_000.0)
        w3 = wave(6, 6, 3_000_000.0)  # widest, most recent - not a contraction

        close = np.concatenate([pre_close, w1[2], w2[2], w3[2], [base_price + 8]])
        high = np.concatenate(
            [pre_close + 0.5, w1[0], w2[0], w3[0], [base_price + 8.2]]
        )
        low = np.concatenate([pre_close - 0.5, w1[1], w2[1], w3[1], [base_price + 1]])
        volume = np.concatenate(
            [np.full(n_pre, 1_000_000.0), w1[3], w2[3], w3[3], [4_000_000.0]]
        )
        expanding = _ohlc(close, high, low, volume)

        out = strategy.generate_signals(expanding)
        assert (out["signal"] == 1).sum() == 0

    def test_no_lookahead_truncation_invariance(self, strategy):
        data = _vcp_setup_data()
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
