"""
Tests for backend/app/quant/indicators.py - Wilder True Range and ATR.

`wilder_atr` is the single source of truth for ATR across strategies, the
regime detector, and the risk sizer (see the module docstring), so these
tests hand-verify the recursive Wilder smoothing formula rather than just
checking shape.
"""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.indicators import true_range, wilder_atr


@pytest.fixture
def ohlc() -> pd.DataFrame:
    index = pd.date_range("2023-01-02", periods=10, freq="B")
    return pd.DataFrame(
        {
            "High": [10, 11, 12, 11, 13, 14, 13, 15, 16, 15],
            "Low": [9, 10, 10, 9, 11, 12, 11, 13, 14, 13],
            "Close": [9.5, 10.5, 11.5, 10, 12.5, 13.5, 12, 14.5, 15.5, 14],
        },
        index=index,
    ).astype(float)


class TestTrueRange:
    def test_first_bar_is_nan(self, ohlc):
        tr = true_range(ohlc)
        assert np.isnan(tr.iloc[0])

    def test_matches_hand_computed_values(self, ohlc):
        tr = true_range(ohlc)
        # Bar 1: high-low=1, |11-9.5|=1.5, |10-9.5|=0.5 -> max=1.5
        assert tr.iloc[1] == pytest.approx(1.5)
        # Bar 4: high-low=2, |13-10|=3, |11-10|=1 -> max=3
        assert tr.iloc[4] == pytest.approx(3.0)

    def test_missing_columns_raises(self):
        with pytest.raises(ValueError, match="missing required column"):
            true_range(pd.DataFrame({"High": [1.0], "Low": [0.5]}))

    def test_empty_df_raises(self):
        with pytest.raises(ValueError, match="empty"):
            true_range(pd.DataFrame(columns=["High", "Low", "Close"]))


class TestWilderATR:
    def test_warmup_window_is_nan(self, ohlc):
        atr = wilder_atr(ohlc, period=3)
        # min_periods defaults to period; first `period` true-range values
        # (index 0 is itself NaN from true_range) stay NaN.
        assert atr.iloc[:3].isna().all()
        assert atr.iloc[3:].notna().all()

    def test_recursive_smoothing_formula(self, ohlc):
        """atr_t = atr_{t-1} + (tr_t - atr_{t-1}) / period, recursively
        seeded at the first non-NaN true-range value itself (`pandas.ewm`'s
        `adjust=False` convention) - verified here against a hand-rolled
        loop so a pandas API change can't silently swap in a different
        (e.g. windowed-mean-seeded) smoothing convention. `min_periods`
        only masks the first `period - 1` of these as NaN in the output;
        the recursion underneath still starts at the series' first value."""
        period = 3
        tr = true_range(ohlc)
        atr = wilder_atr(ohlc, period=period)

        first_valid = tr.first_valid_index()
        running = tr.loc[first_valid]
        start = tr.index.get_loc(first_valid) + 1
        for timestamp in tr.index[start:]:
            running = running + (tr.loc[timestamp] - running) / period
            if pd.notna(atr.loc[timestamp]):
                assert atr.loc[timestamp] == pytest.approx(running)

    def test_period_below_two_raises(self, ohlc):
        with pytest.raises(ValueError, match="at least 2"):
            wilder_atr(ohlc, period=1)

    def test_explicit_min_periods_overrides_default(self, ohlc):
        atr = wilder_atr(ohlc, period=3, min_periods=1)
        assert atr.iloc[1:].notna().all()

    def test_atr_is_always_non_negative(self, ohlc):
        atr = wilder_atr(ohlc, period=3)
        assert (atr.dropna() >= 0).all()
