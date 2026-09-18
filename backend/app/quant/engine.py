"""
Backtest wrapper around the `backtesting` library.

Adapts a `BaseStrategy`'s precomputed signal DataFrame into a
`backtesting.Strategy`, executing trades bar-by-bar with the position sizing
and SL/TP resolution supplied by a `RiskManager`. This engine is long-only:
`signal == 1` opens a long if flat, `signal == -1` closes an open long. It
does not open short positions - see `engine/forward_tester.py` /
AGENTS.md for how to extend this.
"""

from dataclasses import dataclass

import pandas as pd
from backtesting import Backtest, Strategy

from backend.app.quant.risk import RiskManager
from backend.app.quant.strategies.base import BaseStrategy

REQUIRED_OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


@dataclass
class BacktestResult:
    """Result of a single `run_backtest` call."""

    stats: pd.Series
    trades: pd.DataFrame
    equity_curve: pd.DataFrame


def run_backtest(
    strategy: BaseStrategy,
    df: pd.DataFrame,
    risk_manager: RiskManager,
    commission: float = 0.001,
    slippage_pct: float = 0.0005,
) -> BacktestResult:
    """Simulate `strategy` over historical `df` with realistic costs.

    Args:
        strategy: A `BaseStrategy` instance.
        df: OHLCV DataFrame (see AGENTS.md "Data ingestion format").
        risk_manager: Sizes positions and resolves absolute SL/TP prices.
        commission: Round-trip commission rate, e.g. 0.001 for 0.1%.
        slippage_pct: Bid/ask spread applied to fills, e.g. 0.0005 for 0.05%.

    Returns:
        BacktestResult with the summary `stats`, a `trades` DataFrame (the
        `backtesting` library's native columns - Size, EntryPrice, PnL, etc. -
        plus a lowercase `pnl` alias column for `analytics.metrics.compute_metrics`),
        and the `equity_curve`.
    """
    missing = [c for c in REQUIRED_OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"df is missing required OHLCV columns: {missing}")

    signals_df = strategy.generate_signals(df)
    BaseStrategy.validate_output(signals_df)

    signal = signals_df["signal"].to_numpy()
    sl_type = signals_df["sl_type"].to_numpy()
    sl_value = signals_df["sl_value"].to_numpy()
    tp_type = signals_df["tp_type"].to_numpy()
    tp_value = signals_df["tp_value"].to_numpy()
    atr = signals_df["atr"].to_numpy() if "atr" in signals_df.columns else None

    bt_df = df[list(REQUIRED_OHLCV_COLUMNS)].copy()

    class _SignalAdapter(Strategy):
        def init(self):
            pass

        def next(self):
            i = len(self.data) - 1
            sig = signal[i]

            if self.position:
                if sig == -1:
                    self.position.close()
                return

            if sig != 1:
                return

            entry_price = float(self.data.Close[-1])
            atr_value = float(atr[i]) if atr is not None else None

            order = risk_manager.build_order(
                entry_price=entry_price,
                sl_type=sl_type[i],
                sl_value=float(sl_value[i]),
                tp_type=tp_type[i],
                tp_value=float(tp_value[i]),
                direction=1,
                atr=atr_value,
            )

            if order.shares > 0:
                self.buy(size=order.shares, sl=order.stop_loss, tp=order.take_profit)

    bt = Backtest(
        bt_df,
        _SignalAdapter,
        cash=risk_manager.account_equity,
        commission=commission,
        spread=slippage_pct,
        exclusive_orders=True,
    )
    stats = bt.run()

    trades = stats._trades.copy()
    trades["pnl"] = trades["PnL"]

    return BacktestResult(stats=stats, trades=trades, equity_curve=stats._equity_curve)
