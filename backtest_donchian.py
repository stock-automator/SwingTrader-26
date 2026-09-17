#!/usr/bin/env python3
"""
Backtest Donchian Breakout Strategy across the cached universe.

Uses the src.strategies / src.core.risk / src.engine.backtester pipeline
described in AGENTS.md, instead of hand-rolled entry/exit tracking.
"""

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from src.core.risk import RiskManager
from src.engine.backtester import run_backtest
from src.strategies.donchian_breakout import DonchianBreakout

DATA_DIR = Path("data/raw")
INITIAL_CAPITAL = 5000.0
RISK_PER_TRADE = 0.01  # 1% risk
COMMISSION = 0.001  # 0.1%
SLIPPAGE = 0.0005  # 0.05%
MAX_TICKERS = 20


def load_stock(ticker: str) -> Optional[pd.DataFrame]:
    """Load a cached ticker's OHLCV data and flatten MultiIndex columns."""
    path = DATA_DIR / f"{ticker}.parquet"
    if not path.exists():
        return None

    df = pd.read_parquet(path)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    return df.sort_index()


def backtest_ticker(ticker: str) -> Optional[dict]:
    """Run the Donchian breakout backtest for a single ticker."""
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

    n_trades = int(result.stats["# Trades"])
    return {
        "ticker": ticker,
        "trades": n_trades,
        "return_pct": float(result.stats["Return [%]"]),
        "win_rate_pct": float(result.stats["Win Rate [%]"]) if n_trades else 0.0,
        "sharpe": float(result.stats["Sharpe Ratio"]),
        "max_dd_pct": float(result.stats["Max. Drawdown [%]"]),
    }


def main() -> None:
    print("=" * 70)
    print("DONCHIAN BREAKOUT STRATEGY BACKTEST")
    print("=" * 70)
    print()

    stocks = [f.stem for f in DATA_DIR.glob("*.parquet")][:MAX_TICKERS]
    print(f"Testing {len(stocks)} stocks...\n")

    results = []
    for ticker in stocks:
        try:
            result = backtest_ticker(ticker)
        except Exception as exc:
            print(f"x {ticker}: error ({exc})")
            continue

        if result is None:
            continue

        results.append(result)
        print(
            f"+ {result['ticker']}: "
            f"{result['return_pct']:+.1f}% | "
            f"Trades: {result['trades']} | "
            f"WinRate: {result['win_rate_pct']:.0f}% | "
            f"Sharpe: {result['sharpe']:.2f} | "
            f"MaxDD: {result['max_dd_pct']:.1f}%"
        )

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if results:
        avg_return = np.mean([r["return_pct"] for r in results])
        win_rate = len([r for r in results if r["return_pct"] > 0]) / len(results)

        print(f"\nStocks tested: {len(results)}")
        print(f"Avg return: {avg_return:+.1f}%")
        print(f"Win rate: {win_rate:.1%} (stocks with positive return)")
        print(f"Total trades: {sum(r['trades'] for r in results)}")
    else:
        print("No valid results - check data or strategy")


if __name__ == "__main__":
    main()
