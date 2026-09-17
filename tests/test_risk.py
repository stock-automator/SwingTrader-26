"""
Tests for RiskManager: SL/TP resolution and position sizing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.risk import RiskManager


@pytest.fixture
def rm():
    return RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)


class TestConstruction:
    def test_rejects_non_positive_equity(self):
        with pytest.raises(ValueError):
            RiskManager(account_equity=0)

    def test_rejects_invalid_risk_pct(self):
        with pytest.raises(ValueError):
            RiskManager(account_equity=5000, risk_per_trade_pct=1.5)
        with pytest.raises(ValueError):
            RiskManager(account_equity=5000, risk_per_trade_pct=0)


class TestResolveStopLoss:
    def test_percentage_long(self, rm):
        sl = rm.resolve_stop_loss(
            entry_price=100, sl_type="PERCENTAGE", sl_value=0.02, direction=1
        )
        assert sl == pytest.approx(98.0)

    def test_percentage_short(self, rm):
        sl = rm.resolve_stop_loss(
            entry_price=100, sl_type="PERCENTAGE", sl_value=0.02, direction=-1
        )
        assert sl == pytest.approx(102.0)

    def test_fixed_long(self, rm):
        sl = rm.resolve_stop_loss(
            entry_price=100, sl_type="FIXED", sl_value=3.5, direction=1
        )
        assert sl == pytest.approx(96.5)

    def test_atr_long(self, rm):
        sl = rm.resolve_stop_loss(
            entry_price=100, sl_type="ATR", sl_value=1.5, atr=2.0, direction=1
        )
        assert sl == pytest.approx(97.0)

    def test_atr_requires_atr_value(self, rm):
        with pytest.raises(ValueError):
            rm.resolve_stop_loss(entry_price=100, sl_type="ATR", sl_value=1.5, atr=None)

    def test_rejects_unknown_type(self, rm):
        with pytest.raises(ValueError):
            rm.resolve_stop_loss(entry_price=100, sl_type="BOGUS", sl_value=1.5)


class TestResolveTakeProfit:
    def test_percentage_long(self, rm):
        tp = rm.resolve_take_profit(
            entry_price=100, tp_type="PERCENTAGE", tp_value=0.05, direction=1
        )
        assert tp == pytest.approx(105.0)

    def test_percentage_short(self, rm):
        tp = rm.resolve_take_profit(
            entry_price=100, tp_type="PERCENTAGE", tp_value=0.05, direction=-1
        )
        assert tp == pytest.approx(95.0)

    def test_atr_long(self, rm):
        tp = rm.resolve_take_profit(
            entry_price=100, tp_type="ATR", tp_value=3.5, atr=2.0, direction=1
        )
        assert tp == pytest.approx(107.0)


class TestPositionSize:
    def test_basic_sizing(self, rm):
        # risk_amount = 5000 * 0.02 = 100; risk_per_share = 2 -> 50 shares
        shares = rm.position_size(entry_price=100, stop_loss_price=98)
        assert shares == 50

    def test_zero_risk_per_share_returns_zero(self, rm):
        shares = rm.position_size(entry_price=100, stop_loss_price=100)
        assert shares == 0

    def test_rounds_down_to_whole_shares(self, rm):
        # risk_amount = 100; risk_per_share = 3 -> 33.33 -> 33
        shares = rm.position_size(entry_price=100, stop_loss_price=97)
        assert shares == 33


class TestBuildOrder:
    def test_long_order(self, rm):
        order = rm.build_order(
            entry_price=100,
            sl_type="ATR",
            sl_value=1.5,
            tp_type="ATR",
            tp_value=3.5,
            direction=1,
            atr=2.0,
        )
        assert order.stop_loss == pytest.approx(97.0)
        assert order.take_profit == pytest.approx(107.0)
        assert order.risk_per_share == pytest.approx(3.0)
        assert order.shares == int((5000 * 0.02) // 3.0)
        assert order.shares > 0

    def test_short_order_signs_are_inverted(self, rm):
        order = rm.build_order(
            entry_price=100,
            sl_type="PERCENTAGE",
            sl_value=0.02,
            tp_type="PERCENTAGE",
            tp_value=0.05,
            direction=-1,
        )
        assert order.stop_loss > 100
        assert order.take_profit < 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
