"""
Lightweight step-by-step paper-trading engine.

Unlike `engine/backtester.py` (which replays a full historical DataFrame at
once), `ForwardTester` processes one new bar at a time as it arrives from a
live/scheduled data feed. It maintains a single open position, checks it
against each new bar's High/Low to trigger stop-loss/take-profit exits, and
otherwise asks the strategy whether to enter.

Scope: single symbol, in-memory only (no disk persistence), long-only
(mirrors `engine/backtester.py`). See AGENTS.md for how to extend this to a
multi-symbol portfolio or persistent order book.
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from src.core.risk import RiskManager
from src.strategies.base_strategy import BaseStrategy


@dataclass
class Position:
    """An open paper-trading position."""

    entry_time: object
    entry_price: float
    shares: int
    stop_loss: float
    take_profit: float


class ForwardTester:
    """Bar-by-bar paper-trading simulator for a single strategy/symbol.

    Args:
        strategy: A `BaseStrategy` instance.
        risk_manager: Sizes new positions and resolves absolute SL/TP.
        symbol: Label recorded on trade log rows.
        lookback: Number of trailing bars (including the new one) handed to
            `strategy.generate_signals` on each step. `None` uses full
            history.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        risk_manager: RiskManager,
        symbol: str = "SYMBOL",
        lookback: Optional[int] = 252,
    ):
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.symbol = symbol
        self.lookback = lookback

        self._history = pd.DataFrame()
        self._position: Optional[Position] = None
        self._trade_log: list[dict] = []
        self._equity = risk_manager.account_equity
        self._equity_curve: list[dict] = []

    def step(self, bar: pd.Series) -> dict:
        """Process one new OHLCV bar.

        Args:
            bar: A Series with Open/High/Low/Close/Volume, `.name` set to
                the bar's timestamp.

        Returns:
            An event dict: {'timestamp', 'action': 'ENTRY'|'EXIT'|'HOLD', ...}
        """
        self._history = pd.concat([self._history, bar.to_frame().T])

        event = {"timestamp": bar.name, "action": "HOLD"}

        if self._position is not None:
            exit_event = self._check_exit(bar)
            if exit_event is not None:
                event = exit_event

        if self._position is None and event["action"] != "EXIT":
            entry_event = self._check_entry(bar)
            if entry_event is not None:
                event = entry_event

        self._equity_curve.append(
            {"timestamp": bar.name, "equity": self._mark_to_market(bar)}
        )

        return event

    def _check_exit(self, bar: pd.Series) -> Optional[dict]:
        pos = self._position

        exit_price = None
        exit_reason = None
        if bar["Low"] <= pos.stop_loss:
            exit_price = pos.stop_loss
            exit_reason = "SL"
        elif bar["High"] >= pos.take_profit:
            exit_price = pos.take_profit
            exit_reason = "TP"

        if exit_price is None:
            return None

        pnl = (exit_price - pos.entry_price) * pos.shares
        self._equity += pnl

        self._trade_log.append(
            {
                "symbol": self.symbol,
                "entry_time": pos.entry_time,
                "entry_price": pos.entry_price,
                "exit_time": bar.name,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "shares": pos.shares,
                "pnl": pnl,
                "pnl_pct": pnl / (pos.entry_price * pos.shares),
            }
        )
        self._position = None

        return {
            "timestamp": bar.name,
            "action": "EXIT",
            "reason": exit_reason,
            "price": exit_price,
            "pnl": pnl,
        }

    def _check_entry(self, bar: pd.Series) -> Optional[dict]:
        window = self._history.tail(self.lookback) if self.lookback else self._history
        if len(window) < 2:
            return None

        signals_df = self.strategy.generate_signals(window)
        last = signals_df.iloc[-1]

        if last["signal"] != 1:
            return None

        atr_value = (
            float(last["atr"])
            if "atr" in signals_df.columns and pd.notna(last.get("atr"))
            else None
        )
        entry_price = float(bar["Close"])

        order = self.risk_manager.build_order(
            entry_price=entry_price,
            sl_type=last["sl_type"],
            sl_value=float(last["sl_value"]),
            tp_type=last["tp_type"],
            tp_value=float(last["tp_value"]),
            direction=1,
            atr=atr_value,
        )

        if order.shares <= 0:
            return None

        self._position = Position(
            entry_time=bar.name,
            entry_price=entry_price,
            shares=order.shares,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
        )

        return {
            "timestamp": bar.name,
            "action": "ENTRY",
            "price": entry_price,
            "shares": order.shares,
            "stop_loss": order.stop_loss,
            "take_profit": order.take_profit,
        }

    def _mark_to_market(self, bar: pd.Series) -> float:
        equity = self._equity
        if self._position is not None:
            equity += (
                bar["Close"] - self._position.entry_price
            ) * self._position.shares
        return equity

    def get_open_positions(self) -> list[dict]:
        """Currently open positions (0 or 1 - this engine is single-position)."""
        return [vars(self._position)] if self._position is not None else []

    def get_trade_log(self) -> pd.DataFrame:
        """Closed trades so far, one row per exit."""
        return pd.DataFrame(self._trade_log)

    @property
    def equity_curve(self) -> pd.Series:
        """Mark-to-market equity after each processed bar."""
        if not self._equity_curve:
            return pd.Series(dtype=float)
        df = pd.DataFrame(self._equity_curve).set_index("timestamp")
        return df["equity"]
