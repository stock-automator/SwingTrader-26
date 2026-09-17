"""
Tests for src/ui/app.py's pure helper functions.

Never imports/launches Streamlit's runtime - only the plain functions that
`main()` wires into `st.*` widgets, exercised against synthetic fixtures
and tmp files.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategies.base_strategy import BaseStrategy
from src.ui.app import (
    load_ticker_data,
    load_trade_journal,
    load_watchlist,
    profit_by_exit_reason,
    run_interactive_backtest,
    scan_signals,
)


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

    return pd.DataFrame(
        {
            "Open": close - np.abs(np.random.randn(n) * 0.1),
            "High": close + np.abs(np.random.randn(n) * 0.4),
            "Low": close - np.abs(np.random.randn(n) * 0.4),
            "Close": close,
            "Volume": np.random.randint(1_000_000, 5_000_000, n),
        },
        index=dates,
    )


class _AlwaysBuyStrategy(BaseStrategy):
    """Fixed BUY signal on every bar, ATR-based SL/TP. Test-only stand-in
    so scan_signals's wiring can be verified without depending on a real
    strategy's indicator warm-up/crossover logic."""

    def __init__(self):
        super().__init__(name="Always Buy")

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "signal": 1,
                "sl_type": "ATR",
                "sl_value": 1.5,
                "tp_type": "ATR",
                "tp_value": 3.0,
                "atr": 2.0,
            },
            index=df.index,
        )


class TestLoadWatchlist:
    def test_reads_tickers_skipping_blank_lines(self, tmp_path):
        path = tmp_path / "watchlist.txt"
        path.write_text("AAPL\n\nMSFT\n \nGOOG\n")

        assert load_watchlist(str(path)) == ["AAPL", "MSFT", "GOOG"]

    def test_missing_file_returns_empty_list(self, tmp_path):
        assert load_watchlist(str(tmp_path / "nope.txt")) == []


class TestLoadTickerData:
    def test_missing_ticker_returns_none(self, tmp_path):
        assert load_ticker_data("NOPE", str(tmp_path)) is None

    def test_loads_and_sorts_cached_parquet(self, tmp_path, trending_data):
        shuffled = trending_data.sample(frac=1.0, random_state=1)
        shuffled.to_parquet(tmp_path / "AAPL.parquet")

        df = load_ticker_data("AAPL", str(tmp_path))

        assert df is not None
        assert list(df.index) == list(df.index.sort_values())
        assert len(df) == len(trending_data)


class TestScanSignals:
    def test_finds_buy_signal_and_resolves_order(self, tmp_path, trending_data):
        trending_data.to_parquet(tmp_path / "AAPL.parquet")

        signals = scan_signals(_AlwaysBuyStrategy(), ["AAPL"], data_dir=str(tmp_path))

        assert list(signals["ticker"]) == ["AAPL"]
        assert signals.iloc[0]["signal"] == "BUY"
        assert signals.iloc[0]["shares"] > 0
        assert signals.iloc[0]["stop_loss"] < signals.iloc[0]["entry_price"]
        assert signals.iloc[0]["take_profit"] > signals.iloc[0]["entry_price"]

    def test_skips_tickers_with_no_cached_data(self, tmp_path):
        signals = scan_signals(
            _AlwaysBuyStrategy(), ["MISSING"], data_dir=str(tmp_path)
        )
        assert signals.empty
        assert list(signals.columns) == [
            "ticker",
            "signal",
            "entry_price",
            "stop_loss",
            "take_profit",
            "shares",
        ]

    def test_skips_insufficient_history(self, tmp_path, trending_data):
        trending_data.head(10).to_parquet(tmp_path / "AAPL.parquet")
        signals = scan_signals(_AlwaysBuyStrategy(), ["AAPL"], data_dir=str(tmp_path))
        assert signals.empty


class TestRunInteractiveBacktest:
    def test_unknown_strategy_raises(self, tmp_path):
        with pytest.raises(ValueError):
            run_interactive_backtest("NOT_A_STRATEGY", "AAPL", data_dir=str(tmp_path))

    def test_missing_ticker_raises(self, tmp_path):
        with pytest.raises(ValueError):
            run_interactive_backtest(
                "MovingAverageCross", "MISSING", data_dir=str(tmp_path)
            )

    def test_runs_full_pipeline(self, tmp_path, trending_data):
        trending_data.to_parquet(tmp_path / "AAPL.parquet")

        result = run_interactive_backtest(
            "MovingAverageCross", "AAPL", data_dir=str(tmp_path)
        )

        assert "stats" in result
        assert "trades" in result
        assert "equity_curve" in result
        assert "sharpe_ratio" in result["metrics"]
        assert "Equity" in result["equity_curve"].columns

        # `equity` is the single resolved series the UI charts, so the
        # missing-column check isn't duplicated at the call site.
        assert result["equity"] is not None
        pd.testing.assert_series_equal(
            result["equity"], result["equity_curve"]["Equity"]
        )

    def test_date_range_filters_data(self, tmp_path, trending_data):
        trending_data.to_parquet(tmp_path / "AAPL.parquet")

        with pytest.raises(ValueError):
            # A narrow window leaves too few bars to backtest.
            run_interactive_backtest(
                "MovingAverageCross",
                "AAPL",
                start="2023-01-01",
                end="2023-01-10",
                data_dir=str(tmp_path),
            )


class TestLoadTradeJournal:
    def test_reads_existing_journal(self, tmp_path):
        path = tmp_path / "trades.csv"
        pd.DataFrame(
            {
                "id": [1],
                "ticker": ["AAPL"],
                "entry_date": [pd.Timestamp("2023-01-01")],
                "entry_price": [100.0],
                "entry_thesis": [""],
                "signal_strength": [50.0],
                "stop_loss": [95.0],
                "target_1": [110.0],
                "target_2": [120.0],
                "entry_status": ["TAKEN"],
                "skip_reason": [None],
                "actual_entry_date": [pd.Timestamp("2023-01-01")],
                "actual_entry_price": [100.0],
                "exit_date": [pd.Timestamp("2023-01-05")],
                "exit_price": [105.0],
                "exit_reason": ["TP1"],
                "holding_days": [4],
                "pnl": [5.0],
                "pnl_pct": [0.05],
                "r_multiple": [1.0],
                "notes": [""],
                "created_at": [pd.Timestamp("2023-01-01")],
            }
        ).to_csv(path, index=False)

        journal = load_trade_journal(str(path))

        assert len(journal) == 1
        assert journal.iloc[0]["ticker"] == "AAPL"

    def test_missing_journal_returns_empty_frame(self, tmp_path):
        journal = load_trade_journal(str(tmp_path / "nope.csv"))
        assert journal.empty


class TestProfitByExitReason:
    def test_groups_and_sums_pnl(self):
        trades = pd.DataFrame(
            {
                "exit_reason": ["SL", "SL", "TP1", None],
                "pnl": [-10.0, -20.0, 30.0, 0.0],
            }
        )

        attribution = profit_by_exit_reason(trades)
        attribution = attribution.set_index("exit_reason")

        assert attribution.loc["SL", "total_pnl"] == pytest.approx(-30.0)
        assert attribution.loc["SL", "trades"] == 2
        assert attribution.loc["TP1", "total_pnl"] == pytest.approx(30.0)

    def test_no_completed_trades_returns_empty(self):
        trades = pd.DataFrame({"exit_reason": [None, None], "pnl": [0.0, 0.0]})
        assert profit_by_exit_reason(trades).empty


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
