"""
Tests for RegimeDetector: ADX/+DI/-DI-based regime classification.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.regime import (
    REGIME_BEAR_TREND,
    REGIME_BULL_TREND,
    REGIME_CHOPPY,
    REGIME_UNKNOWN,
    RegimeDetector,
)


def _make_ohlc(close: np.ndarray) -> pd.DataFrame:
    close = pd.Series(close)
    idx = pd.date_range("2020-01-01", periods=len(close), freq="D")
    return pd.DataFrame(
        {
            "Open": close.values,
            "High": close.values * 1.004,
            "Low": close.values * 0.996,
            "Close": close.values,
            "Volume": 1_000_000,
        },
        index=idx,
    )


def _trending_data(n: int = 120, slope: float = 1.0) -> pd.DataFrame:
    close = 100 + slope * np.arange(n)
    return _make_ohlc(close)


def _choppy_data(n: int = 120) -> pd.DataFrame:
    # High-frequency zigzag: direction reverses every bar, so ADX stays low.
    close = 100 + np.array([2 if i % 2 == 0 else -2 for i in range(n)], dtype=float)
    return _make_ohlc(close)


@pytest.fixture
def detector():
    return RegimeDetector(adx_period=14, atr_period=14, adx_trend_threshold=25.0)


class TestConstruction:
    def test_rejects_short_periods(self):
        with pytest.raises(ValueError):
            RegimeDetector(adx_period=1)
        with pytest.raises(ValueError):
            RegimeDetector(atr_period=1)

    def test_rejects_non_positive_threshold(self):
        with pytest.raises(ValueError):
            RegimeDetector(adx_trend_threshold=0)


class TestComputeIndicators:
    def test_adds_expected_columns(self, detector):
        out = detector.compute_indicators(_trending_data())
        for col in ("plus_di", "minus_di", "adx", "atr"):
            assert col in out.columns

    def test_warmup_rows_are_nan(self, detector):
        out = detector.compute_indicators(_trending_data())
        assert out["adx"].iloc[:14].isna().all()

    def test_atr_is_wilder_smoothed(self, detector):
        # The exposed `atr` must use the same Wilder smoothing as the ADX
        # path's internal true-range average, not an SMA - downstream
        # volatility-parity sizing consumes this value, so the two must
        # agree on what "ATR" means.
        df = _trending_data()
        out = detector.compute_indicators(df)

        high, low, close = df["High"], df["Low"], df["Close"]
        prev_close = close.shift(1)
        true_range = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        expected = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

        pd.testing.assert_series_equal(
            out["atr"], expected, check_names=False, rtol=1e-12
        )

        sma = true_range.rolling(window=14, min_periods=14).mean()
        assert not np.isclose(out["atr"].iloc[-1], sma.iloc[-1])

    def test_atr_period_is_independent_of_adx_period(self):
        # With the defaults the two periods coincide, so `atr` and the ADX
        # path's internal true-range average are the same series and a bug
        # reading adx_period for the ATR smoothing would be invisible. Split
        # them to pin that atr_period is the one actually used.
        detector = RegimeDetector(adx_period=14, atr_period=30)
        df = _trending_data(n=200)
        out = detector.compute_indicators(df)

        high, low, close = df["High"], df["Low"], df["Close"]
        prev_close = close.shift(1)
        true_range = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)

        expected = true_range.ewm(alpha=1 / 30, adjust=False, min_periods=30).mean()
        pd.testing.assert_series_equal(
            out["atr"], expected, check_names=False, rtol=1e-12
        )

        # Warm-up follows atr_period, not adx_period. min_periods=30 makes
        # bar 29 the first defined ATR; ADX is defined by bar 26 (double
        # Wilder smoothing over 14 bars, so ~2x the period, not 14), which
        # leaves bars 26-28 with an ADX but no ATR. Were the ATR smoothed on
        # adx_period it would already be defined there.
        assert out["atr"].iloc[:29].isna().all()
        assert pd.notna(out["atr"].iloc[29])
        assert pd.notna(out["adx"].iloc[27]) and pd.isna(out["atr"].iloc[27])

        wrong_period = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        assert not np.isclose(out["atr"].iloc[-1], wrong_period.iloc[-1])

    def test_rejects_empty_frame(self, detector):
        with pytest.raises(ValueError, match="empty"):
            detector.compute_indicators(_make_ohlc(np.array([])))

    def test_rejects_missing_columns(self, detector):
        # ValueError, not the bare KeyError the indicator math would raise -
        # matching the convention used elsewhere in src/core.
        df = _trending_data().drop(columns=["High"])
        with pytest.raises(ValueError, match="missing required column"):
            detector.compute_indicators(df)


class TestDetectRegime:
    def test_uptrend_is_bull_trend(self, detector):
        regime = detector.detect_regime(_trending_data(slope=1.0))
        assert regime.iloc[-1] == REGIME_BULL_TREND

    def test_downtrend_is_bear_trend(self, detector):
        regime = detector.detect_regime(_trending_data(slope=-1.0))
        assert regime.iloc[-1] == REGIME_BEAR_TREND

    def test_choppy_market_is_choppy(self, detector):
        regime = detector.detect_regime(_choppy_data())
        assert regime.iloc[-1] == REGIME_CHOPPY

    def test_warmup_rows_are_none(self, detector):
        regime = detector.detect_regime(_trending_data())
        assert regime.iloc[0] is None

    def test_flat_prices_yield_no_regime(self, detector):
        # A dead-flat series gives +DM = -DM = 0, so +DI + -DI = 0 and DX is
        # 0/0. The implementation replaces that zero denominator with NaN, so
        # every bar must come back None rather than a spurious CHOPPY (or an
        # inf that clears the trend threshold).
        regime = detector.detect_regime(_make_ohlc(np.full(120, 100.0)))

        assert regime.isna().all()


class TestCurrentRegime:
    def test_insufficient_data_returns_unknown(self, detector):
        assert detector.current_regime(_trending_data(n=10)) == REGIME_UNKNOWN

    def test_matches_last_row_of_detect_regime(self, detector):
        df = _trending_data()
        assert detector.current_regime(df) == detector.detect_regime(df).iloc[-1]

    def test_flat_prices_return_unknown(self, detector):
        # Covers the `else REGIME_UNKNOWN` fallback: enough bars to clear the
        # length check, but the latest bar still classifies as None because
        # ADX is undefined on a flat series.
        df = _make_ohlc(np.full(120, 100.0))

        assert detector.detect_regime(df).iloc[-1] is None
        assert detector.current_regime(df) == REGIME_UNKNOWN


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
