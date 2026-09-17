"""
Tests for engine/forward_tester.py

Uses a small scripted strategy (signal driven by a sentinel Volume value)
so the test exercises the ForwardTester state machine itself - entry,
stop-loss exit, take-profit exit, trade log, equity curve - independent of
any real indicator logic (those are covered by the strategy-specific tests).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.risk import RiskManager
from src.engine.forward_tester import ForwardTester
from src.strategies.base_strategy import BaseStrategy

ENTRY_SENTINEL_VOLUME = 42


class ScriptedStrategy(BaseStrategy):
    """Emits a BUY signal whenever the latest bar's Volume == sentinel."""

    def __init__(self, sl_pct: float = 0.05, tp_pct: float = 0.20):
        super().__init__(name="Scripted")
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        active = df["Volume"] == ENTRY_SENTINEL_VOLUME

        out = df.copy()
        out["signal"] = active.astype(int)
        out["sl_type"] = np.where(active, "PERCENTAGE", None)
        out["sl_value"] = np.where(active, self.sl_pct, np.nan)
        out["tp_type"] = np.where(active, "PERCENTAGE", None)
        out["tp_value"] = np.where(active, self.tp_pct, np.nan)
        return out


def make_bar(timestamp, close, volume=0, high=None, low=None):
    high = close if high is None else high
    low = close if low is None else low
    return pd.Series(
        {"Open": close, "High": high, "Low": low, "Close": close, "Volume": volume},
        name=pd.Timestamp(timestamp),
    )


@pytest.fixture
def tester():
    strategy = ScriptedStrategy(sl_pct=0.05, tp_pct=0.20)
    risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)
    return ForwardTester(strategy, risk_manager, symbol="TEST")


class TestForwardTester:
    def test_no_action_without_signal(self, tester):
        event = tester.step(make_bar("2024-01-01", close=100))
        assert event["action"] == "HOLD"
        assert tester.get_open_positions() == []

    def test_entry_signal_opens_position(self, tester):
        tester.step(make_bar("2024-01-01", close=100))
        event = tester.step(
            make_bar("2024-01-02", close=100, volume=ENTRY_SENTINEL_VOLUME)
        )

        assert event["action"] == "ENTRY"
        assert event["stop_loss"] == pytest.approx(95.0)
        assert event["take_profit"] == pytest.approx(120.0)

        open_positions = tester.get_open_positions()
        assert len(open_positions) == 1
        assert open_positions[0]["shares"] > 0

    def test_stop_loss_exit_closes_position_and_logs_trade(self, tester):
        tester.step(make_bar("2024-01-01", close=100))
        tester.step(make_bar("2024-01-02", close=100, volume=ENTRY_SENTINEL_VOLUME))

        # Price gaps down through the stop.
        event = tester.step(make_bar("2024-01-03", close=90, high=91, low=90))

        assert event["action"] == "EXIT"
        assert event["reason"] == "SL"
        assert tester.get_open_positions() == []

        trade_log = tester.get_trade_log()
        assert len(trade_log) == 1
        assert trade_log.iloc[0]["exit_reason"] == "SL"
        assert trade_log.iloc[0]["pnl"] < 0

    def test_take_profit_exit_closes_position_and_logs_trade(self, tester):
        tester.step(make_bar("2024-01-01", close=100))
        tester.step(make_bar("2024-01-02", close=100, volume=ENTRY_SENTINEL_VOLUME))

        # Price spikes through the take-profit.
        event = tester.step(make_bar("2024-01-03", close=122, high=122, low=119))

        assert event["action"] == "EXIT"
        assert event["reason"] == "TP"

        trade_log = tester.get_trade_log()
        assert len(trade_log) == 1
        assert trade_log.iloc[0]["exit_reason"] == "TP"
        assert trade_log.iloc[0]["pnl"] > 0

    def test_full_entry_exit_reentry_cycle(self, tester):
        tester.step(make_bar("2024-01-01", close=100))
        tester.step(make_bar("2024-01-02", close=100, volume=ENTRY_SENTINEL_VOLUME))
        tester.step(make_bar("2024-01-03", close=90, high=91, low=90))  # SL exit

        # Flat again - a fresh signal should open a new position.
        event = tester.step(
            make_bar("2024-01-04", close=100, volume=ENTRY_SENTINEL_VOLUME)
        )
        assert event["action"] == "ENTRY"

        event = tester.step(
            make_bar("2024-01-05", close=122, high=122, low=119)
        )  # TP exit
        assert event["action"] == "EXIT"
        assert event["reason"] == "TP"

        trade_log = tester.get_trade_log()
        assert len(trade_log) == 2
        assert list(trade_log["exit_reason"]) == ["SL", "TP"]

    def test_equity_curve_tracks_every_step(self, tester):
        tester.step(make_bar("2024-01-01", close=100))
        tester.step(make_bar("2024-01-02", close=100, volume=ENTRY_SENTINEL_VOLUME))
        tester.step(make_bar("2024-01-03", close=90, high=91, low=90))

        curve = tester.equity_curve
        assert len(curve) == 3
        # Equity drops after the losing trade closes.
        assert curve.iloc[-1] < curve.iloc[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
