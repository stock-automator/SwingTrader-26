"""
Tests for `PositionSizer` (ATR-based sizing formula) and `ChandelierExitStop`
(ATR trailing stop) in `backend.app.quant.risk`.
"""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.risk import ChandelierExitStop, PositionSizer


def _ohlc(n: int = 40, base: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    close = base + np.cumsum(np.random.default_rng(7).normal(0, 1, n))
    high = close + 1.5
    low = close - 1.5
    return pd.DataFrame(
        {"Open": close, "High": high, "Low": low, "Close": close}, index=idx
    )


class TestPositionSizerConstruction:
    def test_rejects_non_positive_capital(self):
        with pytest.raises(ValueError):
            PositionSizer(account_capital=0, risk_pct=0.01)

    def test_rejects_bad_risk_pct(self):
        with pytest.raises(ValueError):
            PositionSizer(account_capital=10_000, risk_pct=0)
        with pytest.raises(ValueError):
            PositionSizer(account_capital=10_000, risk_pct=1.5)


class TestPositionSizerShares:
    def test_formula_matches_capital_times_risk_over_atr_times_multiplier(self):
        sizer = PositionSizer(account_capital=100_000, risk_pct=0.01)
        # risk_amount = 1000; risk_per_share = atr(2.0) * mult(2.0) = 4.0
        shares = sizer.shares(atr=2.0, atr_multiplier=2.0)
        assert shares == 250

    def test_risk_amount_property(self):
        sizer = PositionSizer(account_capital=50_000, risk_pct=0.02)
        assert sizer.risk_amount == pytest.approx(1000.0)

    def test_higher_risk_pct_yields_more_shares(self):
        low = PositionSizer(account_capital=10_000, risk_pct=0.005)
        high = PositionSizer(account_capital=10_000, risk_pct=0.02)
        assert high.shares(atr=1.0) > low.shares(atr=1.0)

    def test_rejects_nonpositive_atr(self):
        sizer = PositionSizer(account_capital=10_000, risk_pct=0.01)
        with pytest.raises(ValueError):
            sizer.shares(atr=0)
        with pytest.raises(ValueError):
            sizer.shares(atr=-1.0)

    def test_rejects_nan_atr(self):
        sizer = PositionSizer(account_capital=10_000, risk_pct=0.01)
        with pytest.raises(ValueError):
            sizer.shares(atr=float("nan"))

    def test_rejects_nonpositive_multiplier(self):
        sizer = PositionSizer(account_capital=10_000, risk_pct=0.01)
        with pytest.raises(ValueError):
            sizer.shares(atr=1.0, atr_multiplier=0)


class TestChandelierExitConstruction:
    def test_rejects_short_period(self):
        with pytest.raises(ValueError):
            ChandelierExitStop(atr_period=1)

    def test_rejects_bad_multiplier(self):
        with pytest.raises(ValueError):
            ChandelierExitStop(atr_multiplier=0)


class TestChandelierExitCompute:
    def test_long_stop_sits_below_highest_high(self):
        df = _ohlc()
        stop = ChandelierExitStop(atr_period=10, atr_multiplier=3.0)
        result = stop.compute(df, direction=1)
        rolling_high = df["High"].rolling(window=10, min_periods=10).max()
        valid = result.dropna()
        assert (valid <= rolling_high.loc[valid.index]).all()

    def test_short_stop_sits_above_lowest_low(self):
        df = _ohlc()
        stop = ChandelierExitStop(atr_period=10, atr_multiplier=3.0)
        result = stop.compute(df, direction=-1)
        rolling_low = df["Low"].rolling(window=10, min_periods=10).min()
        valid = result.dropna()
        assert (valid >= rolling_low.loc[valid.index]).all()

    def test_warmup_bars_are_nan(self):
        df = _ohlc()
        stop = ChandelierExitStop(atr_period=10)
        result = stop.compute(df, direction=1)
        assert result.iloc[:9].isna().all()

    def test_missing_columns_raises(self):
        stop = ChandelierExitStop()
        with pytest.raises(ValueError):
            stop.compute(pd.DataFrame({"Close": [1.0, 2.0]}))

    def test_empty_frame_raises(self):
        stop = ChandelierExitStop()
        with pytest.raises(ValueError):
            stop.compute(pd.DataFrame(columns=["High", "Low", "Close"]))

    def test_bad_direction_raises(self):
        stop = ChandelierExitStop()
        with pytest.raises(ValueError):
            stop.compute(_ohlc(), direction=0)


class TestChandelierExitRatchet:
    def test_long_only_moves_up(self):
        assert ChandelierExitStop.ratchet(100.0, 105.0, direction=1) == 105.0
        assert ChandelierExitStop.ratchet(100.0, 95.0, direction=1) == 100.0

    def test_short_only_moves_down(self):
        assert ChandelierExitStop.ratchet(100.0, 95.0, direction=-1) == 95.0
        assert ChandelierExitStop.ratchet(100.0, 105.0, direction=-1) == 100.0

    def test_bad_direction_raises(self):
        with pytest.raises(ValueError):
            ChandelierExitStop.ratchet(100.0, 105.0, direction=2)
