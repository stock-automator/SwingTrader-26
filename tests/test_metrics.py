"""
Tests for analytics/metrics.py
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.analytics.metrics import (compute_metrics, export_trades_csv,
                                   save_equity_curve_chart)


class TestComputeMetricsTradeStats:
    def test_known_win_rate_and_profit_factor(self):
        trades_df = pd.DataFrame({"pnl": [100, -50, 100, -50]})
        metrics = compute_metrics(trades_df)

        assert metrics["total_trades"] == 4
        assert metrics["win_rate"] == pytest.approx(0.5)
        assert metrics["expectancy"] == pytest.approx(25.0)
        assert metrics["profit_factor"] == pytest.approx(2.0)

    def test_no_trades(self):
        metrics = compute_metrics(pd.DataFrame({"pnl": []}))
        assert metrics["total_trades"] == 0
        assert metrics["win_rate"] == 0.0
        assert metrics["profit_factor"] == 0.0

    def test_all_losing_trades(self):
        trades_df = pd.DataFrame({"pnl": [-10, -20, -5]})
        metrics = compute_metrics(trades_df)

        assert metrics["win_rate"] == 0.0
        assert metrics["profit_factor"] == 0.0
        assert metrics["expectancy"] < 0

    def test_all_winning_trades_profit_factor_is_infinite(self):
        trades_df = pd.DataFrame({"pnl": [10, 20]})
        metrics = compute_metrics(trades_df)
        assert metrics["profit_factor"] == float("inf")


class TestComputeMetricsEquityCurve:
    def test_missing_equity_curve_defaults(self):
        trades_df = pd.DataFrame({"pnl": [10, -5]})
        metrics = compute_metrics(trades_df, equity_curve=None)

        assert metrics["sharpe_ratio"] == 0.0
        assert metrics["sortino_ratio"] == 0.0
        assert metrics["max_drawdown_pct"] == 0.0
        assert metrics["cagr_pct"] is None

    def test_known_max_drawdown(self):
        dates = pd.date_range("2024-01-01", periods=4, freq="D")
        equity = pd.Series([100.0, 110.0, 90.0, 120.0], index=dates)

        metrics = compute_metrics(pd.DataFrame({"pnl": [10]}), equity_curve=equity)

        # (90 - 110) / 110 * 100
        assert metrics["max_drawdown_pct"] == pytest.approx(-18.1818, abs=1e-3)

    def test_monotonic_growth_has_positive_sharpe_and_cagr(self):
        dates = pd.date_range("2023-01-01", periods=300, freq="D")
        equity = pd.Series([5000.0 * (1.001**i) for i in range(300)], index=dates)

        metrics = compute_metrics(pd.DataFrame({"pnl": [10]}), equity_curve=equity)

        assert metrics["sharpe_ratio"] > 0
        assert metrics["cagr_pct"] > 0
        assert metrics["max_drawdown_pct"] == pytest.approx(0.0, abs=1e-6)


class TestExports:
    def test_export_trades_csv_creates_file(self, tmp_path):
        trades_df = pd.DataFrame({"pnl": [10, -5]})
        out = tmp_path / "nested" / "trades.csv"

        export_trades_csv(trades_df, str(out))

        assert out.exists()
        loaded = pd.read_csv(out)
        assert list(loaded["pnl"]) == [10, -5]

    def test_save_equity_curve_chart_creates_png(self, tmp_path):
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        equity = pd.Series(range(5000, 5010), index=dates, dtype=float)
        out = tmp_path / "charts" / "equity.png"

        save_equity_curve_chart(equity, str(out))

        assert out.exists()
        assert out.stat().st_size > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
