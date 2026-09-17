#!/usr/bin/env python3
"""
Backtest Donchian Breakout with detailed per-trade exit reporting.

Uses the src.strategies / src.core.risk / src.engine.backtester pipeline
described in AGENTS.md - stop-loss and take-profit exits are handled by the
`backtesting` library itself rather than a hand-rolled bar loop.
"""

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from src.analytics.console import render_table
from src.core.risk import RiskManager
from src.engine.backtester import run_backtest
from src.strategies.donchian_breakout import DonchianBreakout

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path("data/raw")
INITIAL_CAPITAL = 5000.0
RISK_PER_TRADE = 0.01  # 1% risk
COMMISSION = 0.001  # 0.1%
SLIPPAGE = 0.0005  # 0.05%
MAX_TICKERS = 10


def load_stock(ticker: str) -> Optional[pd.DataFrame]:
    """Load a cached ticker's OHLCV data and flatten MultiIndex columns."""
    path = DATA_DIR / f"{ticker}.parquet"
    if not path.exists():
        return None

    df = pd.read_parquet(path)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    return df.sort_index()


def backtest_ticker(ticker: str) -> Optional[tuple]:
    """Run the backtest for a single ticker; returns (summary, trades_df)."""
    df = load_stock(ticker)
    if df is None or len(df) < 100:
        log.warning("%s: skipped (insufficient cached history)", ticker)
        return None

    risk_manager = RiskManager(
        account_equity=INITIAL_CAPITAL, risk_per_trade_pct=RISK_PER_TRADE
    )
    result = run_backtest(
        DonchianBreakout(),
        df,
        risk_manager,
        commission=COMMISSION,
        slippage_pct=SLIPPAGE,
    )

    trades = result.trades.copy()
    trades["ticker"] = ticker

    n_trades = len(trades)
    wins = int((trades["pnl"] > 0).sum()) if n_trades else 0
    losses = n_trades - wins

    summary = {
        "ticker": ticker,
        "trades": n_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n_trades if n_trades else 0.0,
        "avg_trade_pct": float(trades["ReturnPct"].mean()) if n_trades else 0.0,
    }
    return summary, trades


def main() -> None:
    stocks = [f.stem for f in DATA_DIR.glob("*.parquet")][:MAX_TICKERS]
    log.info("Testing %d stocks", len(stocks))

    summaries = []
    all_trades = []
    for ticker in stocks:
        try:
            outcome = backtest_ticker(ticker)
        except Exception:
            log.error("%s: backtest failed", ticker, exc_info=True)
            continue

        if outcome is None:
            continue

        summary, trades = outcome
        summaries.append(summary)
        all_trades.append(trades)

    if not summaries:
        log.error("No valid results - check data or strategy")
        return

    rows = [
        [
            s["ticker"],
            str(s["trades"]),
            f"{s['wins']}/{s['losses']}",
            f"{s['win_rate']:.0%}",
            f"{s['avg_trade_pct']:+.2%}",
        ]
        for s in summaries
    ]
    print()
    print(
        render_table(
            ["Ticker", "Trades", "W/L", "Win Rate", "Avg Trade"],
            rows,
            title="DONCHIAN BREAKOUT - EXIT DETAIL",
        )
    )

    total_trades = sum(s["trades"] for s in summaries)
    total_wins = sum(s["wins"] for s in summaries)
    total_losses = sum(s["losses"] for s in summaries)
    overall_wr = total_wins / total_trades if total_trades else 0.0

    summary_rows = [
        ["Total Trades", str(total_trades)],
        ["Wins / Losses", f"{total_wins} / {total_losses}"],
        ["Overall Win Rate", f"{overall_wr:.1%}"],
    ]
    print()
    print(render_table(["Metric", "Value"], summary_rows, title="SUMMARY"))

    combined = pd.concat(all_trades) if all_trades else pd.DataFrame()
    if not combined.empty:
        combined = combined.sort_values("EntryTime")
        trade_rows = [
            [
                t["ticker"],
                t["EntryTime"].date().isoformat(),
                f"${t['EntryPrice']:.2f}",
                f"${t['ExitPrice']:.2f}",
                f"{t['ReturnPct']:+.2%}",
            ]
            for _, t in combined.head(5).iterrows()
        ]
        print()
        print(
            render_table(
                ["Ticker", "Entry Date", "Entry", "Exit", "Return"],
                trade_rows,
                title="FIRST 5 TRADES",
            )
        )
    print()


if __name__ == "__main__":
    main()
