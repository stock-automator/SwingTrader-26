#!/usr/bin/env python3
"""
Backtest Donchian Breakout Strategy across the cached universe.

Uses the src.strategies / src.core.risk / src.engine.backtester pipeline
described in AGENTS.md, instead of hand-rolled entry/exit tracking.
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np
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
    stocks = [f.stem for f in DATA_DIR.glob("*.parquet")][:MAX_TICKERS]
    log.info("Testing %d stocks", len(stocks))

    results = []
    for ticker in stocks:
        try:
            result = backtest_ticker(ticker)
        except Exception:
            log.error("%s: backtest failed", ticker, exc_info=True)
            continue

        if result is not None:
            results.append(result)

    if not results:
        log.error("No valid results - check data or strategy")
        return

    rows = [
        [
            r["ticker"],
            f"{r['return_pct']:+.1f}%",
            str(r["trades"]),
            f"{r['win_rate_pct']:.0f}%",
            f"{r['sharpe']:.2f}",
            f"{r['max_dd_pct']:.1f}%",
        ]
        for r in sorted(results, key=lambda r: r["return_pct"], reverse=True)
    ]
    print()
    print(
        render_table(
            ["Ticker", "Return", "Trades", "Win Rate", "Sharpe", "Max DD"],
            rows,
            title="DONCHIAN BREAKOUT STRATEGY BACKTEST",
        )
    )

    avg_return = np.mean([r["return_pct"] for r in results])
    win_rate = len([r for r in results if r["return_pct"] > 0]) / len(results)
    summary_rows = [
        ["Stocks Tested", str(len(results))],
        ["Avg Return", f"{avg_return:+.1f}%"],
        ["Win Rate (stocks profitable)", f"{win_rate:.1%}"],
        ["Total Trades", str(sum(r["trades"] for r in results))],
    ]
    print()
    print(render_table(["Metric", "Value"], summary_rows, title="SUMMARY"))
    print()


if __name__ == "__main__":
    main()
