#!/usr/bin/env python3
"""
Backtest Donchian Breakout with detailed per-trade exit reporting.

Uses the src.strategies / src.core.risk / src.engine.backtester pipeline
described in AGENTS.md - stop-loss and take-profit exits are handled by the
`backtesting` library itself rather than a hand-rolled bar loop.
"""

from pathlib import Path
from typing import Optional

import pandas as pd

from src.core.risk import RiskManager
from src.engine.backtester import run_backtest
from src.strategies.donchian_breakout import DonchianBreakout

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
    print("=" * 70)
    print("DONCHIAN BREAKOUT STRATEGY BACKTEST - WITH EXIT LOGIC")
    print("=" * 70)
    print()

    stocks = [f.stem for f in DATA_DIR.glob("*.parquet")][:MAX_TICKERS]
    print(f"Testing {len(stocks)} stocks...\n")

    summaries = []
    all_trades = []
    for ticker in stocks:
        outcome = backtest_ticker(ticker)
        if outcome is None:
            continue

        summary, trades = outcome
        summaries.append(summary)
        all_trades.append(trades)

        print(
            f"+ {summary['ticker']}: "
            f"Trades={summary['trades']} | "
            f"W/L={summary['wins']}/{summary['losses']} | "
            f"WR={summary['win_rate']:.0%} | "
            f"AvgTrade={summary['avg_trade_pct']:+.2%}"
        )

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if summaries:
        total_trades = sum(s["trades"] for s in summaries)
        total_wins = sum(s["wins"] for s in summaries)
        total_losses = sum(s["losses"] for s in summaries)
        overall_wr = total_wins / total_trades if total_trades else 0.0

        print(f"\nTotal trades: {total_trades}")
        print(f"Total wins: {total_wins} | Total losses: {total_losses}")
        print(f"Overall win rate: {overall_wr:.1%}")

        combined = pd.concat(all_trades) if all_trades else pd.DataFrame()
        if not combined.empty:
            combined = combined.sort_values("EntryTime")
            print("\n" + "=" * 70)
            print("FIRST 5 TRADES")
            print("=" * 70)
            for i, (_, t) in enumerate(combined.head(5).iterrows()):
                print(f"{i + 1}. {t['ticker']} ({t['EntryTime'].date()})")
                print(f"   Entry: ${t['EntryPrice']:.2f} | Exit: ${t['ExitPrice']:.2f}")
                print(f"   PnL: {t['ReturnPct']:+.2%}")
                print()
    else:
        print("No valid results - check data or strategy")


if __name__ == "__main__":
    main()
