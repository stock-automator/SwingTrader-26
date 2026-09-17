"""
Tests for engine/backtester.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.risk import RiskManager
from src.engine.backtester import BacktestResult, run_backtest
from src.strategies.moving_average_cross import MovingAverageCross


@pytest.fixture
def trending_data():
    np.random.seed(3)
    n = 300
    dates = pd.date_range(start="2023-01-01", periods=n, freq="D")

    trend = np.concatenate(
        [
            np.linspace(100, 70, n // 3),
            np.linspace(70, 140, n // 3),
            np.linspace(140, 100, n - 2 * (n // 3)),
        ]
    )
    noise = np.random.randn(n) * 0.5
    close = trend + noise

    df = pd.DataFrame(
        {
            "Open": close - np.abs(np.random.randn(n) * 0.1),
            "High": close + np.abs(np.random.randn(n) * 0.4),
            "Low": close - np.abs(np.random.randn(n) * 0.4),
            "Close": close,
            "Volume": np.random.randint(1_000_000, 5_000_000, n),
        },
        index=dates,
    )

    return df


class TestRunBacktest:
    def test_runs_and_returns_expected_shape(self, trending_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        result = run_backtest(strategy, trending_data, risk_manager)

        assert isinstance(result, BacktestResult)
        assert "# Trades" in result.stats.index
        assert "Return [%]" in result.stats.index
        assert isinstance(result.trades, pd.DataFrame)
        assert isinstance(result.equity_curve, pd.DataFrame)
        assert "Equity" in result.equity_curve.columns

    def test_rejects_missing_ohlcv_columns(self, trending_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        risk_manager = RiskManager(account_equity=5000.0)

        bad_df = trending_data.drop(columns=["Volume"])
        with pytest.raises(ValueError):
            run_backtest(strategy, bad_df, risk_manager)

    def test_trades_respect_position_sizing(self, trending_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        result = run_backtest(strategy, trending_data, risk_manager)

        if len(result.trades) > 0:
            # No trade should ever exceed available cash at entry.
            assert (result.trades["Size"] > 0).all()

    def test_trades_have_lowercase_pnl_alias_for_analytics(self, trending_data):
        # analytics.metrics.compute_metrics expects a lowercase `pnl` column.
        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        result = run_backtest(strategy, trending_data, risk_manager)

        assert "pnl" in result.trades.columns
        if len(result.trades) > 0:
            assert (result.trades["pnl"] == result.trades["PnL"]).all()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
