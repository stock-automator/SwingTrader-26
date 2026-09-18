"""
Tests for quant/monte_carlo.py: trade-sequence bootstrap simulation.
"""

import numpy as np
import pytest

from backend.app.quant.monte_carlo import (
    DEFAULT_N_SIMULATIONS,
    MonteCarloResult,
    run_monte_carlo,
)


class TestValidation:
    def test_rejects_empty_trades(self):
        with pytest.raises(ValueError, match="non-empty"):
            run_monte_carlo([], initial_capital=1000.0)

    def test_rejects_non_positive_simulations(self):
        with pytest.raises(ValueError, match="n_simulations"):
            run_monte_carlo([10.0], n_simulations=0)

    def test_rejects_non_positive_initial_capital(self):
        with pytest.raises(ValueError, match="initial_capital"):
            run_monte_carlo([10.0], initial_capital=0)

    def test_rejects_out_of_range_ruin_threshold(self):
        with pytest.raises(ValueError, match="ruin_threshold_pct"):
            run_monte_carlo([10.0], ruin_threshold_pct=1.0)
        with pytest.raises(ValueError, match="ruin_threshold_pct"):
            run_monte_carlo([10.0], ruin_threshold_pct=-0.1)


class TestRunMonteCarlo:
    def test_returns_result_with_expected_shape(self):
        trades = [50.0, -20.0, 30.0, -10.0, 40.0] * 20  # 100 trades
        result = run_monte_carlo(
            trades, initial_capital=1000.0, n_simulations=200, seed=1
        )

        assert isinstance(result, MonteCarloResult)
        assert result.n_simulations == 200
        assert result.n_trades == 100
        for p in ("p05", "p25", "p50", "p75", "p95"):
            assert p in result.equity_curve_percentiles
            assert len(result.equity_curve_percentiles[p]) == 101  # +1 for bar 0

    def test_deterministic_with_seed(self):
        trades = [50.0, -20.0, 30.0, -10.0]
        a = run_monte_carlo(trades, n_simulations=500, seed=42)
        b = run_monte_carlo(trades, n_simulations=500, seed=42)

        assert a.as_dict() == b.as_dict()

    def test_default_n_simulations_meets_product_minimum(self):
        assert DEFAULT_N_SIMULATIONS >= 1000

    def test_all_winning_trades_has_zero_risk_of_ruin(self):
        trades = [10.0, 20.0, 30.0] * 10
        result = run_monte_carlo(
            trades, initial_capital=1000.0, n_simulations=500, seed=3
        )
        assert result.risk_of_ruin_pct == 0.0
        assert result.max_drawdown_p95_pct == pytest.approx(0.0)

    def test_all_losing_trades_eventually_ruins_every_path(self):
        trades = [-50.0] * 10
        result = run_monte_carlo(
            trades,
            initial_capital=1000.0,
            n_simulations=500,
            ruin_threshold_pct=0.5,
            seed=4,
        )
        # 10 trades of -50 = -500 total, exactly the 50% ruin level -> every
        # path (with or without replacement) ends at or below it.
        assert result.risk_of_ruin_pct == 100.0

    def test_without_replacement_uses_every_trade_exactly_once(self):
        trades = [100.0, -50.0, 25.0, -10.0, 5.0]
        result = run_monte_carlo(
            trades,
            initial_capital=1000.0,
            n_simulations=300,
            with_replacement=False,
            seed=7,
        )
        # Every path is a permutation of the same trades, so every path's
        # final equity is identical (order doesn't change the sum).
        expected_final = 1000.0 + sum(trades)
        assert result.mean_final_equity == pytest.approx(expected_final)
        assert result.median_final_equity == pytest.approx(expected_final)
        assert result.final_equity_p05 == pytest.approx(expected_final)
        assert result.final_equity_p95 == pytest.approx(expected_final)

    def test_with_replacement_produces_final_equity_variance(self):
        trades = [100.0, -80.0, 60.0, -40.0, 20.0]
        result = run_monte_carlo(
            trades,
            initial_capital=1000.0,
            n_simulations=1000,
            with_replacement=True,
            seed=9,
        )
        # A bootstrap can draw the same trade repeatedly, so unlike the
        # without-replacement case, final equity should vary across paths.
        assert result.final_equity_p05 != pytest.approx(result.final_equity_p95)

    def test_drawdown_percentiles_are_non_negative_and_ordered(self):
        rng = np.random.default_rng(11)
        trades = rng.normal(5, 50, 200).tolist()
        result = run_monte_carlo(
            trades, initial_capital=2000.0, n_simulations=500, seed=11
        )
        assert result.max_drawdown_p95_pct >= 0
        assert result.max_drawdown_p99_pct >= result.max_drawdown_p95_pct

    def test_equity_curve_percentiles_start_at_initial_capital(self):
        trades = [10.0, -5.0, 20.0]
        result = run_monte_carlo(
            trades, initial_capital=500.0, n_simulations=100, seed=13
        )
        for curve in result.equity_curve_percentiles.values():
            assert curve[0] == pytest.approx(500.0)

    def test_higher_ruin_threshold_never_decreases_risk_of_ruin(self):
        rng = np.random.default_rng(17)
        trades = rng.normal(0, 30, 150).tolist()
        low = run_monte_carlo(
            trades,
            initial_capital=1000.0,
            ruin_threshold_pct=0.2,
            n_simulations=500,
            seed=17,
        )
        high = run_monte_carlo(
            trades,
            initial_capital=1000.0,
            ruin_threshold_pct=0.8,
            n_simulations=500,
            seed=17,
        )
        assert high.risk_of_ruin_pct >= low.risk_of_ruin_pct

    def test_as_dict_is_json_ready(self):
        result = run_monte_carlo([10.0, -5.0], n_simulations=100, seed=1)
        payload = result.as_dict()
        assert payload["n_simulations"] == 100
        assert isinstance(payload["risk_of_ruin_pct"], float)
        assert isinstance(payload["equity_curve_percentiles"]["p50"], list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
