"""
Tests for quant/analytics.py: factor exposure via quantstats.

quantstats is an optional dependency - these tests skip (rather than fail)
if it isn't installed, matching how `save_tearsheet` in quant/backtest.py
treats the same optional extra.
"""

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("quantstats")

from backend.app.quant.analytics import (  # noqa: E402
    FactorExposure,
    compute_factor_exposure,
)


def _returns(n: int, mean: float, std: float, seed: int) -> pd.Series:
    idx = pd.date_range("2022-01-01", periods=n, freq="B")
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mean, std, n), index=idx)


class TestComputeFactorExposure:
    def test_returns_a_fully_populated_result(self):
        strategy = _returns(300, 0.001, 0.01, seed=1)
        benchmark = _returns(300, 0.0005, 0.008, seed=2)

        result = compute_factor_exposure(strategy, benchmark, risk_free_rate=0.02)

        assert isinstance(result, FactorExposure)
        for field in (
            result.alpha_annual_pct,
            result.beta,
            result.sharpe_ratio,
            result.sortino_ratio,
            result.calmar_ratio,
            result.tail_ratio,
        ):
            assert field is not None
            assert np.isfinite(field)

    def test_rejects_empty_strategy_returns(self):
        benchmark = _returns(50, 0.0, 0.01, seed=3)
        with pytest.raises(ValueError, match="non-empty"):
            compute_factor_exposure(pd.Series(dtype=float), benchmark)

    def test_rejects_empty_benchmark_returns(self):
        strategy = _returns(50, 0.0, 0.01, seed=4)
        with pytest.raises(ValueError, match="non-empty"):
            compute_factor_exposure(strategy, pd.Series(dtype=float))

    def test_rejects_non_overlapping_date_ranges(self):
        strategy = pd.Series(
            [0.01, 0.02], index=pd.date_range("2020-01-01", periods=2, freq="B")
        )
        benchmark = pd.Series(
            [0.01, 0.02], index=pd.date_range("2023-01-01", periods=2, freq="B")
        )
        with pytest.raises(ValueError, match="share no dates"):
            compute_factor_exposure(strategy, benchmark)

    def test_only_uses_common_dates(self):
        # Benchmark has extra leading/trailing dates the strategy doesn't -
        # the result should still compute cleanly off the overlap.
        strategy = _returns(200, 0.0008, 0.01, seed=5)
        wider_idx = pd.date_range("2021-06-01", periods=400, freq="B")
        rng = np.random.default_rng(6)
        benchmark = pd.Series(rng.normal(0.0004, 0.008, 400), index=wider_idx)

        result = compute_factor_exposure(strategy, benchmark)
        assert result.beta is not None

    def test_beta_near_one_for_a_perfectly_tracking_strategy(self):
        benchmark = _returns(300, 0.0006, 0.01, seed=7)
        strategy = benchmark.copy()  # identical returns -> beta == 1, alpha == 0

        result = compute_factor_exposure(strategy, benchmark)
        assert result.beta == pytest.approx(1.0, abs=1e-6)
        assert result.alpha_annual_pct == pytest.approx(0.0, abs=1e-6)

    def test_as_dict_is_json_ready(self):
        strategy = _returns(250, 0.0007, 0.01, seed=8)
        benchmark = _returns(250, 0.0004, 0.008, seed=9)
        payload = compute_factor_exposure(strategy, benchmark).as_dict()

        assert set(payload) == {
            "alpha_annual_pct",
            "beta",
            "sharpe_ratio",
            "sortino_ratio",
            "calmar_ratio",
            "tail_ratio",
        }


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
