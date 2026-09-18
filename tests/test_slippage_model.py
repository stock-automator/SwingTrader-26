"""Unit tests for `quant.slippage_model` - dynamic per-bar spread and
volume-based market impact (see `test_replay_api.py` for the wired-in
`simulate-trade-execution` behavior)."""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.slippage_model import (
    SlippageBreakdown,
    estimate_dynamic_spread_pct,
    estimate_execution_drag,
    estimate_market_impact_pct,
    spread_variance_pct,
    trailing_avg_volume,
)


def _ohlcv(n: int = 60, seed: int = 1, vol_regime: float = 1.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2023-01-02", periods=n, freq="B")
    noise = rng.normal(0, vol_regime, n).cumsum()
    close = pd.Series(100.0 + noise, index=index).clip(lower=5.0)
    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close + vol_regime,
            "Low": close - vol_regime,
            "Close": close,
            "Volume": pd.Series(500_000.0, index=index),
        },
        index=index,
    )


class TestEstimateDynamicSpreadPct:
    def test_rejects_negative_atr_multiple(self):
        df = _ohlcv()
        with pytest.raises(ValueError):
            estimate_dynamic_spread_pct(df, 40, atr_multiple=-0.1)

    def test_rejects_out_of_range_idx(self):
        df = _ohlcv(n=30)
        with pytest.raises(ValueError):
            estimate_dynamic_spread_pct(df, 999)

    def test_warmup_bar_is_zero(self):
        df = _ohlcv()
        assert estimate_dynamic_spread_pct(df, 0) == 0.0

    def test_positive_after_warmup(self):
        df = _ohlcv()
        spread = estimate_dynamic_spread_pct(df, len(df) - 1, atr_multiple=0.1)
        assert spread > 0.0

    def test_more_volatile_regime_gets_wider_spread(self):
        calm = _ohlcv(seed=1, vol_regime=0.3)
        wild = _ohlcv(seed=1, vol_regime=5.0)
        idx = len(calm) - 1
        assert estimate_dynamic_spread_pct(wild, idx) > estimate_dynamic_spread_pct(
            calm, idx
        )

    def test_differs_bar_to_bar_within_one_ticker(self):
        """The whole point of "dynamic" vs. engine.estimate_atr_spread_pct's
        whole-history mean: two bars in the same series can price
        differently."""
        df = _ohlcv(n=120, seed=3, vol_regime=2.0)
        spreads = {i: estimate_dynamic_spread_pct(df, i) for i in range(20, 120, 10)}
        assert len(set(spreads.values())) > 1


class TestSpreadVariancePct:
    def test_rejects_bad_lookback(self):
        df = _ohlcv()
        with pytest.raises(ValueError):
            spread_variance_pct(df, 40, lookback=0)

    def test_non_negative(self):
        df = _ohlcv()
        assert spread_variance_pct(df, len(df) - 1) >= 0.0

    def test_zero_for_degenerate_single_bar_window(self):
        df = _ohlcv()
        assert spread_variance_pct(df, 0, lookback=20) == 0.0


class TestTrailingAvgVolume:
    def test_rejects_bad_lookback(self):
        df = _ohlcv()
        with pytest.raises(ValueError):
            trailing_avg_volume(df, 10, lookback=0)

    def test_matches_mean_of_window(self):
        df = _ohlcv()
        assert trailing_avg_volume(df, 30, lookback=10) == pytest.approx(500_000.0)


class TestEstimateMarketImpactPct:
    def test_zero_coefficient_disables_impact(self):
        assert (
            estimate_market_impact_pct(10_000, 500_000, impact_coefficient=0.0) == 0.0
        )

    def test_zero_shares_or_volume_is_zero(self):
        assert estimate_market_impact_pct(0, 500_000, impact_coefficient=0.1) == 0.0
        assert estimate_market_impact_pct(1000, 0, impact_coefficient=0.1) == 0.0

    def test_larger_order_costs_more(self):
        small = estimate_market_impact_pct(1_000, 500_000, impact_coefficient=0.1)
        large = estimate_market_impact_pct(100_000, 500_000, impact_coefficient=0.1)
        assert large > small

    def test_sqrt_scaling_not_linear(self):
        """Square-root model: 4x the participation should be ~2x the
        impact, not 4x - the defining property of this model vs. a linear
        one."""
        base = estimate_market_impact_pct(
            10_000, 500_000, impact_coefficient=0.1, max_impact_pct=1.0
        )
        quadrupled = estimate_market_impact_pct(
            40_000, 500_000, impact_coefficient=0.1, max_impact_pct=1.0
        )
        assert quadrupled == pytest.approx(base * 2, rel=1e-6)

    def test_capped_at_max_impact_pct(self):
        impact = estimate_market_impact_pct(
            10_000_000, 1_000, impact_coefficient=10.0, max_impact_pct=0.05
        )
        assert impact == 0.05

    def test_rejects_negative_params(self):
        with pytest.raises(ValueError):
            estimate_market_impact_pct(1000, 500_000, impact_coefficient=-0.1)
        with pytest.raises(ValueError):
            estimate_market_impact_pct(1000, 500_000, max_impact_pct=-0.1)


class TestSlippageBreakdown:
    def test_total_is_sum_of_components(self):
        breakdown = SlippageBreakdown(
            spread_pct=0.001, market_impact_pct=0.002, spread_variance_pct=0.0005
        )
        assert breakdown.total_slippage_pct == pytest.approx(0.003)

    def test_as_dict_rounds_and_includes_total(self):
        breakdown = SlippageBreakdown(
            spread_pct=0.0011234567, market_impact_pct=0.0, spread_variance_pct=0.0
        )
        payload = breakdown.as_dict()
        assert payload["spread_pct"] == round(0.0011234567, 6)
        assert payload["total_slippage_pct"] == payload["spread_pct"]


class TestEstimateExecutionDrag:
    def test_disabled_impact_by_default(self):
        df = _ohlcv()
        breakdown = estimate_execution_drag(df, len(df) - 1, shares=10_000)
        assert breakdown.market_impact_pct == 0.0

    def test_impact_included_when_coefficient_set(self):
        df = _ohlcv()
        breakdown = estimate_execution_drag(
            df, len(df) - 1, shares=100_000, impact_coefficient=0.2
        )
        assert breakdown.market_impact_pct > 0.0
        assert breakdown.total_slippage_pct > breakdown.spread_pct
