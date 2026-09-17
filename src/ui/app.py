"""
Streamlit dashboard for SwingTrader-26.

Three tabs backed by the existing `src/core` / `src/engine` / `src/journal`
pipeline - no strategy, risk, or metrics logic is reimplemented here:
  - Daily Signal Scanner: today's BUY/SELL signals across the watchlist.
  - Interactive Backtester: run any registered strategy over a cached
    ticker and date range, view the equity curve and Sharpe/drawdown.
  - Trade Journal & Analytics: the live trade journal and profit-by-exit-
    reason attribution.

Split into pure helper functions (no `streamlit` import required to call
them - see `tests/test_ui_helpers.py`) and a thin `main()` that wires them
into `st.*` widgets, so the underlying logic is testable without a running
Streamlit session.
"""

from pathlib import Path
from typing import Optional

import pandas as pd

from src.analytics.metrics import compute_metrics
from src.core.risk import RiskManager
from src.engine.backtester import run_backtest
from src.journal.executor import TradeJournal
from src.strategies.base_strategy import BaseStrategy
from src.strategies.donchian_breakout import DonchianBreakout
from src.strategies.moving_average_cross import MovingAverageCross

#: Strategies selectable in the dashboard, keyed by display name.
STRATEGIES = {
    "MovingAverageCross": MovingAverageCross,
    "DonchianBreakout": DonchianBreakout,
}

DEFAULT_ACCOUNT_EQUITY = 5000.0
DEFAULT_RISK_PER_TRADE_PCT = 0.02


def load_watchlist(path: str = "config/watchlist.txt") -> list[str]:
    """Read one ticker per line from `path`, skipping blank lines."""
    watchlist_path = Path(path)
    if not watchlist_path.exists():
        return []
    return [
        line.strip() for line in watchlist_path.read_text().splitlines() if line.strip()
    ]


def load_ticker_data(ticker: str, data_dir: str = "data/raw") -> Optional[pd.DataFrame]:
    """Load a cached ticker's OHLCV parquet, flattening a MultiIndex if present.

    Returns `None` if no cached data exists for `ticker`.
    """
    path = Path(data_dir) / f"{ticker}.parquet"
    if not path.exists():
        return None

    df = pd.read_parquet(path)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    return df.sort_index()


def scan_signals(
    strategy: BaseStrategy,
    tickers: list[str],
    data_dir: str = "data/raw",
    account_equity: float = DEFAULT_ACCOUNT_EQUITY,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
) -> pd.DataFrame:
    """Run `strategy` over each ticker's cached history and collect the
    latest-bar BUY/SELL signals with resolved entry/stop/target prices.

    Tickers with no cached data, insufficient history, or whose signal
    can't be resolved into a sized order are silently skipped - a scanner
    is expected to run over a heterogeneous universe.

    Returns:
        DataFrame with columns `ticker`, `signal` ('BUY'/'SELL'),
        `entry_price`, `stop_loss`, `take_profit`, `shares`.
    """
    risk_manager = RiskManager(account_equity, risk_per_trade_pct)
    rows = []

    for ticker in tickers:
        df = load_ticker_data(ticker, data_dir)
        if df is None or len(df) < 100:
            continue

        signals_df = strategy.generate_signals(df)
        BaseStrategy.validate_output(signals_df)

        latest = signals_df.iloc[-1]
        if latest["signal"] == 0:
            continue

        direction = int(latest["signal"])
        atr = (
            float(latest["atr"])
            if "atr" in signals_df.columns and pd.notna(latest["atr"])
            else None
        )

        try:
            order = risk_manager.build_order(
                entry_price=float(df["Close"].iloc[-1]),
                sl_type=latest["sl_type"],
                sl_value=float(latest["sl_value"]),
                tp_type=latest["tp_type"],
                tp_value=float(latest["tp_value"]),
                direction=direction,
                atr=atr,
            )
        except ValueError:
            continue

        rows.append(
            {
                "ticker": ticker,
                "signal": "BUY" if direction == 1 else "SELL",
                "entry_price": order.entry_price,
                "stop_loss": order.stop_loss,
                "take_profit": order.take_profit,
                "shares": order.shares,
            }
        )

    return pd.DataFrame(
        rows,
        columns=[
            "ticker",
            "signal",
            "entry_price",
            "stop_loss",
            "take_profit",
            "shares",
        ],
    )


def run_interactive_backtest(
    strategy_name: str,
    ticker: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account_equity: float = DEFAULT_ACCOUNT_EQUITY,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    data_dir: str = "data/raw",
) -> dict:
    """Load `ticker`'s cached data, slice to `[start, end]`, and run a
    full backtest for `strategy_name`.

    Args:
        strategy_name: Key into `STRATEGIES`.
        start, end: Inclusive date bounds (any `pd.Timestamp`-parsable
            string), or `None` for no bound.

    Returns:
        Dict with `stats`, `trades`, `equity_curve` (see
        `engine.backtester.BacktestResult`), `metrics` (see
        `analytics.metrics.compute_metrics`), and `equity` - the resolved
        `Equity` series, or `None` if the curve has no such column. Callers
        should chart `equity` rather than re-indexing `equity_curve`, so the
        missing-column check lives in one place.

    Raises:
        ValueError: unknown strategy, no cached data for `ticker`, or too
            little history in the selected date range to backtest.
    """
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy_name!r}")

    df = load_ticker_data(ticker, data_dir)
    if df is None:
        raise ValueError(f"No cached data for {ticker!r}")

    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end)]

    if len(df) < 100:
        raise ValueError(
            f"{ticker!r} has only {len(df)} bars in the selected range, need at least 100"
        )

    strategy = STRATEGIES[strategy_name]()
    risk_manager = RiskManager(account_equity, risk_per_trade_pct)
    result = run_backtest(strategy, df, risk_manager)

    equity = result.equity_curve["Equity"] if "Equity" in result.equity_curve else None
    metrics = compute_metrics(result.trades, equity)

    return {
        "stats": result.stats,
        "trades": result.trades,
        "equity_curve": result.equity_curve,
        "equity": equity,
        "metrics": metrics,
    }


def load_trade_journal(path: str = "data/trades_live.csv") -> pd.DataFrame:
    """Thin wrapper over `TradeJournal` returning the raw journal DataFrame."""
    return TradeJournal(journal_file=path).df


def profit_by_exit_reason(trades: pd.DataFrame) -> pd.DataFrame:
    """Total/average `pnl` grouped by `exit_reason`, for completed trades only."""
    completed = trades[trades["exit_reason"].notna()]
    if len(completed) == 0:
        return pd.DataFrame(columns=["exit_reason", "total_pnl", "avg_pnl", "trades"])

    grouped = completed.groupby("exit_reason")["pnl"].agg(["sum", "mean", "count"])
    grouped = grouped.rename(
        columns={"sum": "total_pnl", "mean": "avg_pnl", "count": "trades"}
    )
    return grouped.reset_index()


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="SwingTrader-26", layout="wide")
    st.title("SwingTrader-26")

    scanner_tab, backtest_tab, journal_tab = st.tabs(
        ["Daily Signal Scanner", "Interactive Backtester", "Trade Journal & Analytics"]
    )

    with scanner_tab:
        st.subheader("Daily Signal Scanner")
        strategy_name = st.selectbox(
            "Strategy", list(STRATEGIES.keys()), key="scanner_strategy"
        )
        watchlist = load_watchlist()
        st.caption(f"Scanning {len(watchlist)} watchlist tickers")

        if st.button("Run Scan"):
            with st.spinner("Scanning..."):
                signals = scan_signals(STRATEGIES[strategy_name](), watchlist)
            if signals.empty:
                st.info("No BUY/SELL signals on the latest bar.")
            else:
                st.dataframe(signals, use_container_width=True)

    with backtest_tab:
        st.subheader("Interactive Backtester")
        col1, col2 = st.columns(2)
        with col1:
            strategy_name = st.selectbox(
                "Strategy", list(STRATEGIES.keys()), key="backtest_strategy"
            )
            ticker = st.selectbox("Symbol", load_watchlist(), key="backtest_ticker")
        with col2:
            start_date = st.date_input("Start date", value=None, key="backtest_start")
            end_date = st.date_input("End date", value=None, key="backtest_end")

        if st.button("Run Backtest"):
            try:
                with st.spinner("Running backtest..."):
                    result = run_interactive_backtest(
                        strategy_name,
                        ticker,
                        start=str(start_date) if start_date else None,
                        end=str(end_date) if end_date else None,
                    )
            except ValueError as exc:
                st.error(str(exc))
            else:
                metrics = result["metrics"]
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Sharpe Ratio", f"{metrics['sharpe_ratio']:.2f}")
                m2.metric("Max Drawdown", f"{metrics['max_drawdown_pct']:.1f}%")
                m3.metric("Win Rate", f"{metrics['win_rate']:.1%}")
                m4.metric("Total Trades", str(metrics["total_trades"]))

                if result["equity"] is not None:
                    st.line_chart(result["equity"])
                else:
                    st.warning("Backtest returned no equity curve to chart.")
                st.dataframe(result["trades"], use_container_width=True)

    with journal_tab:
        st.subheader("Trade Journal & Analytics")
        journal = load_trade_journal()
        if journal.empty:
            st.info("No trades logged yet.")
        else:
            st.dataframe(journal, use_container_width=True)
            attribution = profit_by_exit_reason(journal)
            if not attribution.empty:
                st.bar_chart(attribution.set_index("exit_reason")["total_pnl"])


if __name__ == "__main__":
    main()
