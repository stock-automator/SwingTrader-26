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


class _FixedSignalStrategy(BaseStrategy):
    """Emits one caller-chosen signal/SL definition on every bar.

    Lets the scanner's non-BUY branches be exercised directly instead of
    hunting for real price data that happens to produce them.
    """

    def __init__(self, signal: int = 1, sl_value: float = 1.5):
        super().__init__(name="Fixed Signal")
        self.signal = signal
        self.sl_value = sl_value

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "signal": self.signal,
                "sl_type": "ATR",
                "sl_value": self.sl_value,
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

    def test_flattens_multiindex_columns(self, tmp_path, trending_data):
        # yfinance returns ('Close', 'AAPL')-style MultiIndex columns, so this
        # is the shape every real cached parquet in data/raw actually has -
        # the single-level fixture above exercises the branch that production
        # data never takes.
        multi = trending_data.copy()
        multi.columns = pd.MultiIndex.from_product([multi.columns, ["AAPL"]])
        multi.to_parquet(tmp_path / "AAPL.parquet")

        df = load_ticker_data("AAPL", str(tmp_path))

        assert df is not None
        assert not isinstance(df.columns, pd.MultiIndex)
        assert list(df.columns) == list(trending_data.columns)
        # Flattening must keep the values addressable by plain column name,
        # since that is how every downstream strategy indexes them.
        assert df["Close"].iloc[-1] == pytest.approx(trending_data["Close"].iloc[-1])

    def test_multiindex_data_is_scannable_end_to_end(self, tmp_path, trending_data):
        # Guards the seam: flattening is only worth anything if the result
        # satisfies the BaseStrategy contract downstream.
        multi = trending_data.copy()
        multi.columns = pd.MultiIndex.from_product([multi.columns, ["AAPL"]])
        multi.to_parquet(tmp_path / "AAPL.parquet")

        signals = scan_signals(_AlwaysBuyStrategy(), ["AAPL"], data_dir=str(tmp_path))

        assert list(signals["ticker"]) == ["AAPL"]
        assert signals.iloc[0]["shares"] > 0


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

    def test_bearish_signal_is_an_unsized_exit_not_a_short(
        self, tmp_path, trending_data
    ):
        # This repo is long-only: both engines treat signal == -1 as "close a
        # long", never "open a short" (see engine/backtester.py and
        # engine/forward_tester.py). Sizing a -1 row and inverting its stop
        # would display a short position the system cannot take.
        trending_data.to_parquet(tmp_path / "AAPL.parquet")

        signals = scan_signals(
            _FixedSignalStrategy(signal=-1), ["AAPL"], data_dir=str(tmp_path)
        )
        row = signals.iloc[0]

        assert row["signal"] == "EXIT LONG"
        assert row["entry_price"] == pytest.approx(
            float(trending_data["Close"].iloc[-1])
        )
        # Unsized: an exit closes whatever is open, so there is no share
        # count or bracket to compute here.
        assert pd.isna(row["shares"])
        assert pd.isna(row["stop_loss"])
        assert pd.isna(row["take_profit"])

    def test_no_signal_produces_no_row(self, tmp_path, trending_data):
        trending_data.to_parquet(tmp_path / "AAPL.parquet")

        signals = scan_signals(
            _FixedSignalStrategy(signal=0), ["AAPL"], data_dir=str(tmp_path)
        )

        assert signals.empty

    def test_nan_sl_value_is_skipped_not_crashed(self, tmp_path, trending_data):
        # A gappy or halted series yields NaN ATR and so NaN sl_value on a bar
        # still flagged active. BaseStrategy.validate_output rejects that, and
        # the rejection has to be per-ticker: unguarded it aborts the scan and
        # one bad symbol costs the other 500.
        trending_data.to_parquet(tmp_path / "AAPL.parquet")

        signals = scan_signals(
            _FixedSignalStrategy(sl_value=float("nan")),
            ["AAPL"],
            data_dir=str(tmp_path),
        )

        assert signals.empty
        assert signals.attrs["skipped"] == 1
        assert any(
            "invalid strategy output" in reason
            for reason in signals.attrs["skip_reasons"]
        )

    def test_invalid_ticker_does_not_abort_the_remaining_scan(
        self, tmp_path, trending_data
    ):
        # The ordering matters: the bad ticker is scanned *first*, so a
        # non-per-ticker guard would mean GOOD never gets looked at.
        trending_data.to_parquet(tmp_path / "BAD.parquet")
        trending_data.to_parquet(tmp_path / "GOOD.parquet")

        class _NanForOneTicker(BaseStrategy):
            """NaN sl_value only for the frame it has seen fewest times -
            i.e. the first ticker scanned."""

            def __init__(self):
                super().__init__(name="Nan For One")
                self.calls = 0

            def generate_signals(self, df):
                self.calls += 1
                bad = self.calls == 1
                return pd.DataFrame(
                    {
                        "signal": 1,
                        "sl_type": "ATR",
                        "sl_value": float("nan") if bad else 1.5,
                        "tp_type": "ATR",
                        "tp_value": 3.0,
                        "atr": 2.0,
                    },
                    index=df.index,
                )

        signals = scan_signals(
            _NanForOneTicker(), ["BAD", "GOOD"], data_dir=str(tmp_path)
        )

        assert list(signals["ticker"]) == ["GOOD"]
        assert signals.attrs["skipped"] == 1

    def test_one_bad_ticker_does_not_block_the_others(self, tmp_path, trending_data):
        trending_data.to_parquet(tmp_path / "GOOD.parquet")
        trending_data.head(10).to_parquet(tmp_path / "SHORT.parquet")

        signals = scan_signals(
            _AlwaysBuyStrategy(),
            ["SHORT", "GOOD", "MISSING"],
            data_dir=str(tmp_path),
        )

        assert list(signals["ticker"]) == ["GOOD"]
        assert signals.attrs["skipped"] == 2
        assert signals.attrs["skip_reasons"] == {
            "insufficient history": 1,
            "no cached data": 1,
        }

    def test_skip_accounting_is_present_even_when_nothing_is_skipped(
        self, tmp_path, trending_data
    ):
        # main() reads these unconditionally, so they must always exist.
        trending_data.to_parquet(tmp_path / "AAPL.parquet")

        signals = scan_signals(_AlwaysBuyStrategy(), ["AAPL"], data_dir=str(tmp_path))

        assert signals.attrs["skipped"] == 0
        assert signals.attrs["skip_reasons"] == {}


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

    def test_window_too_narrow_to_backtest_raises(self, tmp_path, trending_data):
        trending_data.to_parquet(tmp_path / "AAPL.parquet")

        with pytest.raises(ValueError, match="need at least"):
            # 10 bars is under MIN_BARS, so there is no usable history.
            run_interactive_backtest(
                "MovingAverageCross",
                "AAPL",
                start="2023-01-01",
                end="2023-01-10",
                data_dir=str(tmp_path),
            )

    def test_date_range_filters_data(self, tmp_path, trending_data):
        # The previous version of this test only asserted that a too-narrow
        # window raised - which says nothing about whether start/end are
        # applied at all. These two windows are both wide enough to run, so
        # the results have to actually differ.
        trending_data.to_parquet(tmp_path / "AAPL.parquet")

        first_half = run_interactive_backtest(
            "MovingAverageCross",
            "AAPL",
            start="2023-01-01",
            end="2023-06-30",
            data_dir=str(tmp_path),
        )
        full = run_interactive_backtest(
            "MovingAverageCross", "AAPL", data_dir=str(tmp_path)
        )

        assert len(first_half["equity_curve"]) < len(full["equity_curve"])
        assert first_half["equity_curve"].index.max() <= pd.Timestamp("2023-06-30")
        assert full["equity_curve"].index.max() > pd.Timestamp("2023-06-30")

    def test_start_without_end_is_honored(self, tmp_path, trending_data):
        trending_data.to_parquet(tmp_path / "AAPL.parquet")

        result = run_interactive_backtest(
            "MovingAverageCross", "AAPL", start="2023-04-01", data_dir=str(tmp_path)
        )

        assert result["equity_curve"].index.min() >= pd.Timestamp("2023-04-01")


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
