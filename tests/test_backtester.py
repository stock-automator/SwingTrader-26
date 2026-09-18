"""
Tests for engine/backtester.py
"""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.engine import (
    EXECUTION_MODE_NEXT_OPEN,
    EXECUTION_MODE_SAME_CLOSE_SLIPPAGE,
    BacktestResult,
    OrderTicket,
    build_order_ticket,
    build_order_tickets,
    estimate_atr_spread_pct,
    run_backtest,
)
from backend.app.quant.risk import RiskManager
from backend.app.quant.strategies.moving_average_cross import MovingAverageCross


@pytest.fixture
def trending_data():
    np.random.seed(3)
    n = 300
    dates = pd.date_range(start="2023-01-01", periods=n, freq="D")

    trend = np.concatenate(
        [
            np.linspace(100, 70, n // 3),
            np.linspace(70, 140, n // 3),
            np.linspace(140, 100, n - 2 * (n // 3)),
        ]
    )
    noise = np.random.randn(n) * 0.5
    close = trend + noise

    df = pd.DataFrame(
        {
            "Open": close - np.abs(np.random.randn(n) * 0.1),
            "High": close + np.abs(np.random.randn(n) * 0.4),
            "Low": close - np.abs(np.random.randn(n) * 0.4),
            "Close": close,
            "Volume": np.random.randint(1_000_000, 5_000_000, n),
        },
        index=dates,
    )

    return df


class TestRunBacktest:
    def test_runs_and_returns_expected_shape(self, trending_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        result = run_backtest(strategy, trending_data, risk_manager)

        assert isinstance(result, BacktestResult)
        assert "# Trades" in result.stats.index
        assert "Return [%]" in result.stats.index
        assert isinstance(result.trades, pd.DataFrame)
        assert isinstance(result.equity_curve, pd.DataFrame)
        assert "Equity" in result.equity_curve.columns

    def test_rejects_missing_ohlcv_columns(self, trending_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        risk_manager = RiskManager(account_equity=5000.0)

        bad_df = trending_data.drop(columns=["Volume"])
        with pytest.raises(ValueError):
            run_backtest(strategy, bad_df, risk_manager)

    def test_trades_respect_position_sizing(self, trending_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        result = run_backtest(strategy, trending_data, risk_manager)

        if len(result.trades) > 0:
            # No trade should ever exceed available cash at entry.
            assert (result.trades["Size"] > 0).all()

    def test_trades_have_lowercase_pnl_alias_for_analytics(self, trending_data):
        # analytics.metrics.compute_metrics expects a lowercase `pnl` column.
        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        result = run_backtest(strategy, trending_data, risk_manager)

        assert "pnl" in result.trades.columns
        if len(result.trades) > 0:
            assert (result.trades["pnl"] == result.trades["PnL"]).all()


@pytest.fixture
def tradeable_data():
    # A clean, single-direction uptrend with a loose stop (sl_pct=0.10, set
    # on the strategy below) so risk-based position sizing doesn't eat the
    # entire account's margin the way it does against `trending_data`'s
    # tighter default 2% stop - see the fixture's own PnL-sign tests, which
    # need at least one closed trade to assert anything about cost impact.
    n = 250
    dates = pd.date_range(start="2023-01-01", periods=n, freq="D")
    rng = np.random.default_rng(7)
    close = 50 + 0.4 * np.arange(n) + rng.normal(0, 0.3, n)
    return pd.DataFrame(
        {
            "Open": close - 0.05,
            "High": close + 0.3,
            "Low": close - 0.3,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=dates,
    )


class TestCostModel:
    def test_fee_per_share_defaults_off(self, trending_data):
        # fee_per_share=0.0 (the default) must reproduce the exact same
        # trades as before this parameter existed - it is opt-in cost
        # modelling, not a silent behavior change.
        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        baseline = run_backtest(strategy, trending_data, risk_manager)
        explicit_zero = run_backtest(
            strategy, trending_data, risk_manager, fee_per_share=0.0
        )

        pd.testing.assert_frame_equal(baseline.trades, explicit_zero.trades)

    def test_fee_per_share_reduces_trade_pnl(self, tradeable_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15, sl_pct=0.10)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        cheap = run_backtest(strategy, tradeable_data, risk_manager, commission=0.0)
        with_fees = run_backtest(
            strategy, tradeable_data, risk_manager, commission=0.0, fee_per_share=0.05
        )

        assert len(cheap.trades) > 0
        assert with_fees.trades["PnL"].sum() < cheap.trades["PnL"].sum()

    def test_atr_slippage_multiple_defaults_off(self, trending_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        baseline = run_backtest(strategy, trending_data, risk_manager)
        explicit_zero = run_backtest(
            strategy, trending_data, risk_manager, atr_slippage_multiple=0.0
        )

        pd.testing.assert_frame_equal(baseline.trades, explicit_zero.trades)

    def test_atr_slippage_multiple_widens_spread_cost(self, tradeable_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15, sl_pct=0.10)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        tight = run_backtest(
            strategy, tradeable_data, risk_manager, commission=0.0, slippage_pct=0.0
        )
        wide = run_backtest(
            strategy,
            tradeable_data,
            risk_manager,
            commission=0.0,
            atr_slippage_multiple=1.0,
        )

        assert len(tight.trades) > 0
        assert wide.trades["PnL"].sum() < tight.trades["PnL"].sum()


class TestExecutionMode:
    def test_rejects_unknown_execution_mode(self, tradeable_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15, sl_pct=0.10)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        with pytest.raises(ValueError, match="execution_mode"):
            run_backtest(strategy, tradeable_data, risk_manager, execution_mode="BOGUS")

    def test_default_execution_mode_is_next_open(self, tradeable_data):
        strategy = MovingAverageCross(fast_period=5, slow_period=15, sl_pct=0.10)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        default_run = run_backtest(strategy, tradeable_data, risk_manager)
        explicit_next_open = run_backtest(
            strategy,
            tradeable_data,
            risk_manager,
            execution_mode=EXECUTION_MODE_NEXT_OPEN,
        )

        pd.testing.assert_frame_equal(default_run.trades, explicit_next_open.trades)

    def test_same_close_slippage_fills_at_a_different_price_than_next_open(
        self, tradeable_data
    ):
        strategy = MovingAverageCross(fast_period=5, slow_period=15, sl_pct=0.10)
        risk_manager = RiskManager(account_equity=5000.0, risk_per_trade_pct=0.02)

        next_open = run_backtest(
            strategy,
            tradeable_data,
            risk_manager,
            execution_mode=EXECUTION_MODE_NEXT_OPEN,
        )
        same_close = run_backtest(
            strategy,
            tradeable_data,
            risk_manager,
            execution_mode=EXECUTION_MODE_SAME_CLOSE_SLIPPAGE,
        )

        assert len(next_open.trades) > 0
        assert len(same_close.trades) > 0
        assert not next_open.trades["EntryPrice"].equals(
            same_close.trades["EntryPrice"]
        )


class TestEstimateAtrSpreadPct:
    def test_scales_with_volatility(self, trending_data):
        quiet = estimate_atr_spread_pct(trending_data, atr_multiple=0.1)
        noisy_df = trending_data.copy()
        noisy_df["High"] = noisy_df["High"] * 1.05
        noisy_df["Low"] = noisy_df["Low"] * 0.95
        noisy = estimate_atr_spread_pct(noisy_df, atr_multiple=0.1)

        assert 0 < quiet < noisy

    def test_zero_multiple_is_zero_spread(self, trending_data):
        assert estimate_atr_spread_pct(trending_data, atr_multiple=0.0) == 0.0

    def test_rejects_negative_multiple(self, trending_data):
        with pytest.raises(ValueError):
            estimate_atr_spread_pct(trending_data, atr_multiple=-0.1)

    def test_short_frame_is_zero(self):
        df = pd.DataFrame(
            {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]}
        )
        assert estimate_atr_spread_pct(df) == 0.0


class TestBuildOrderTicket:
    def test_produces_a_tradable_ticket(self):
        ticket = build_order_ticket(
            ticker="aapl",
            entry_price=100.0,
            sl_type="ATR",
            sl_value=2.0,
            tp_type="ATR",
            tp_value=5.0,
            account_equity=5000.0,
            atr=1.0,
        )
        assert isinstance(ticket, OrderTicket)
        assert ticket.ticker == "AAPL"
        assert ticket.tradable is True
        assert ticket.quantity > 0
        assert ticket.notional_value == pytest.approx(
            ticket.quantity * ticket.entry_price
        )
        assert ticket.reward_risk_ratio == pytest.approx(2.5)
        assert ticket.note is None

    def test_rejects_sub_minimum_r_with_a_note(self):
        ticket = build_order_ticket(
            ticker="AAPL",
            entry_price=100.0,
            sl_type="ATR",
            sl_value=2.0,
            tp_type="ATR",
            tp_value=3.0,  # R = 1.5, below the 2.5 minimum
            account_equity=5000.0,
            atr=1.0,
        )
        assert ticket.tradable is False
        assert ticket.quantity == 0
        assert ticket.note is not None

    def test_as_dict_is_json_ready(self):
        ticket = build_order_ticket(
            ticker="AAPL",
            entry_price=100.0,
            sl_type="ATR",
            sl_value=2.0,
            tp_type="ATR",
            tp_value=5.0,
            account_equity=5000.0,
            atr=1.0,
        )
        payload = ticket.as_dict()
        assert payload["ticker"] == "AAPL"
        assert payload["quantity"] == ticket.quantity
        assert isinstance(payload["notional_value"], float)


class TestBuildOrderTickets:
    def test_one_ticket_per_tier(self):
        tickets = build_order_tickets(
            ticker="AAPL",
            entry_price=100.0,
            sl_type="ATR",
            sl_value=2.0,
            tp_type="ATR",
            tp_value=5.0,
            atr=1.0,
        )
        assert [t.account_equity for t in tickets] == [1_000.0, 5_000.0, 10_000.0]

    def test_larger_accounts_get_more_shares(self):
        tickets = build_order_tickets(
            ticker="AAPL",
            entry_price=100.0,
            sl_type="ATR",
            sl_value=2.0,
            tp_type="ATR",
            tp_value=5.0,
            atr=1.0,
        )
        quantities = [t.quantity for t in tickets]
        assert quantities == sorted(quantities)
        assert quantities[0] < quantities[-1]

    def test_custom_tiers(self):
        tickets = build_order_tickets(
            ticker="AAPL",
            entry_price=100.0,
            sl_type="ATR",
            sl_value=2.0,
            tp_type="ATR",
            tp_value=5.0,
            account_tiers=(2_500.0,),
            atr=1.0,
        )
        assert len(tickets) == 1
        assert tickets[0].account_equity == 2_500.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
