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

    def test_rejects_unknown_sizing_method(self, rm):
        with pytest.raises(ValueError):
            rm.build_order(
                entry_price=100,
                sl_type="PERCENTAGE",
                sl_value=0.02,
                tp_type="PERCENTAGE",
                tp_value=0.05,
                sizing_method="bogus",
            )

    def test_vol_parity_requires_atr(self, rm):
        with pytest.raises(ValueError):
            rm.build_order(
                entry_price=100,
                sl_type="PERCENTAGE",
                sl_value=0.02,
                tp_type="PERCENTAGE",
                tp_value=0.05,
                sizing_method="vol_parity",
            )

    def test_vol_parity_differs_from_fixed_risk(self, rm):
        # fixed_risk sizes off the resolved stop distance (0.02 * 100 = 2.0/share);
        # vol_parity sizes off atr_multiple * atr (2.0 * 3.0 = 6.0/share) instead.
        fixed = rm.build_order(
            entry_price=100,
            sl_type="PERCENTAGE",
            sl_value=0.02,
            tp_type="PERCENTAGE",
            tp_value=0.05,
            atr=3.0,
            sizing_method="fixed_risk",
        )
        vol_parity = rm.build_order(
            entry_price=100,
            sl_type="PERCENTAGE",
            sl_value=0.02,
            tp_type="PERCENTAGE",
            tp_value=0.05,
            atr=3.0,
            sizing_method="vol_parity",
        )
        assert fixed.shares != vol_parity.shares
        assert vol_parity.shares == int((5000 * 0.02) // (2.0 * 3.0))
        # SL/TP resolution is unaffected by sizing_method.
        assert fixed.stop_loss == vol_parity.stop_loss
        assert fixed.take_profit == vol_parity.take_profit

    def test_vol_parity_is_capped_at_the_fixed_risk_size(self, rm):
        # The breach case: a quiet asset (atr 0.5 -> 1.0/share of assumed
        # risk) behind a distant stop (10% of 100 -> 10.0/share of actual
        # risk). Raw parity wants 100 // 1.0 = 100 shares, which against the
        # real stop would lose 100 * 10.0 = 1000.0 - 20% of a 5000 account,
        # 10x the 2% budget. The cap holds it to the fixed-risk size.
        order = rm.build_order(
            entry_price=100,
            sl_type="PERCENTAGE",
            sl_value=0.10,
            tp_type="PERCENTAGE",
            tp_value=0.20,
            atr=0.5,
            sizing_method="vol_parity",
        )
        raw_parity = rm.volatility_parity_size(atr=0.5, atr_multiple=2.0)

        assert raw_parity == 100
        assert order.shares == 10  # == the fixed-risk size, not 100
        assert order.shares * order.risk_per_share == pytest.approx(100.0)

    @pytest.mark.parametrize("sizing_method", ["fixed_risk", "vol_parity"])
    @pytest.mark.parametrize("sl_value", [0.005, 0.02, 0.10, 0.35])
    def test_risk_amount_never_exceeds_the_budget(self, rm, sizing_method, sl_value):
        # The module's headline invariant, swept across stop distances either
        # side of atr_multiple * atr so both the capped and uncapped branches
        # are covered: a stopped-out trade loses at most risk_per_trade_pct.
        order = rm.build_order(
            entry_price=100,
            sl_type="PERCENTAGE",
            sl_value=sl_value,
            tp_type="PERCENTAGE",
            tp_value=0.50,
            atr=0.5,
            sizing_method=sizing_method,
        )
        budget = 5000 * 0.02

        assert order.shares * order.risk_per_share <= budget + 1e-9

    @pytest.mark.parametrize("sizing_method", ["fixed_risk", "vol_parity"])
    def test_risk_amount_reports_actual_not_budgeted_risk(self, rm, sizing_method):
        # risk_amount must agree with shares * risk_per_share. Reporting the
        # flat budget here understates a position whose share count got
        # rounded down (or capped), which is what a consumer displaying
        # risk_amount to a trader would show them.
        order = rm.build_order(
            entry_price=100,
            sl_type="PERCENTAGE",
            sl_value=0.03,
            tp_type="PERCENTAGE",
            tp_value=0.09,
            atr=0.5,
            sizing_method=sizing_method,
        )

        assert order.risk_amount == pytest.approx(order.shares * order.risk_per_share)
        # 100 // 3.0 = 33 shares, so 99.0 of actual risk against a 100.0
        # budget - close enough to be missed, different enough to matter.
        assert order.risk_amount == pytest.approx(99.0)

    def test_rejects_nan_sl_value(self, rm):
        # A strategy can emit NaN sl_value on an active bar. Without an
        # explicit guard this reaches int(nan) and surfaces as "cannot
        # convert float NaN to integer" from inside position_size.
        with pytest.raises(ValueError, match="value must be finite"):
            rm.build_order(
                entry_price=100,
                sl_type="PERCENTAGE",
                sl_value=float("nan"),
                tp_type="PERCENTAGE",
                tp_value=0.05,
            )

    def test_rejects_nan_atr_for_atr_based_levels(self, rm):
        with pytest.raises(ValueError, match="atr must be finite"):
            rm.build_order(
                entry_price=100,
                sl_type="ATR",
                sl_value=1.5,
                tp_type="ATR",
                tp_value=3.0,
                atr=float("nan"),
            )


class TestVolatilityParitySize:
    @pytest.mark.parametrize(
        "atr,atr_multiple,expected",
        [
            (2.0, 2.0, int((5000 * 0.02) // 4.0)),
            (1.0, 2.0, int((5000 * 0.02) // 2.0)),
            (5.0, 1.0, int((5000 * 0.02) // 5.0)),
        ],
    )
    def test_table_driven_sizing(self, rm, atr, atr_multiple, expected):
        assert rm.volatility_parity_size(atr, atr_multiple) == expected

    def test_rejects_zero_atr(self, rm):
        with pytest.raises(ValueError):
            rm.volatility_parity_size(atr=0)

    def test_rejects_negative_atr(self, rm):
        with pytest.raises(ValueError):
            rm.volatility_parity_size(atr=-1.0)

    def test_rejects_non_positive_atr_multiple(self, rm):
        with pytest.raises(ValueError):
            rm.volatility_parity_size(atr=2.0, atr_multiple=0)

    @pytest.mark.parametrize(
        "kwargs,match",
        [
            ({"atr": float("nan")}, "atr must be finite"),
            ({"atr": float("inf")}, "atr must be finite"),
            ({"atr": 2.0, "atr_multiple": float("nan")}, "atr_multiple must be finite"),
        ],
    )
    def test_rejects_non_finite_inputs(self, rm, kwargs, match):
        # NaN is False against every comparison, so a bare `<= 0` check lets
        # it through and it resurfaces as an opaque "cannot convert float NaN
        # to integer" from the int() at the end of this method.
        with pytest.raises(ValueError, match=match):
            rm.volatility_parity_size(**kwargs)

    def test_smaller_atr_yields_more_shares(self, rm):
        low_vol_shares = rm.volatility_parity_size(atr=1.0)
        high_vol_shares = rm.volatility_parity_size(atr=5.0)
        assert low_vol_shares > high_vol_shares


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
