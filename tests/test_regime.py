"""
Tests for RegimeDetector: ADX/+DI/-DI-based regime classification.
"""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.regime import (
    MACRO_BEAR_TRENDING,
    MACRO_BULL_TRENDING,
    MACRO_HIGH_VOLATILITY_CHOP,
    MACRO_NEUTRAL,
    MACRO_REGIME_UNKNOWN,
    REGIME_BEAR_TREND,
    REGIME_BULL_TREND,
    REGIME_CHOPPY,
    REGIME_UNKNOWN,
    MacroRegimeDetector,
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
        ).max(axis=1, skipna=False)
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
        ).max(axis=1, skipna=False)

        expected = true_range.ewm(alpha=1 / 30, adjust=False, min_periods=30).mean()
        pd.testing.assert_series_equal(
            out["atr"], expected, check_names=False, rtol=1e-12
        )

        # Warm-up follows atr_period, not adx_period. True range is NaN on
        # the series' first bar (no previous close), so min_periods=30 needs
        # 30 valid true-range observations starting at bar 1 - bar 30 is the
        # first defined ATR. ADX is defined by bar 27 (double Wilder
        # smoothing over 14 bars, so ~2x the period, not 14), which leaves
        # bars 27-29 with an ADX but no ATR. Were the ATR smoothed on
        # adx_period it would already be defined there.
        assert out["atr"].iloc[:30].isna().all()
        assert pd.notna(out["atr"].iloc[30])
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


def _macro_ohlc(close: np.ndarray) -> pd.DataFrame:
    close = pd.Series(close)
    idx = pd.date_range("2020-01-01", periods=len(close), freq="D")
    return pd.DataFrame(
        {
            "Open": close.values,
            "High": close.values * 1.002,
            "Low": close.values * 0.998,
            "Close": close.values,
            "Volume": 1_000_000,
        },
        index=idx,
    )


def _macro_bull(n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    close = 100 + 0.3 * np.arange(n) + rng.normal(0, 0.05, n)
    return _macro_ohlc(close)


def _macro_bear(n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(2)
    close = 200 - 0.3 * np.arange(n) + rng.normal(0, 0.05, n)
    return _macro_ohlc(close)


def _macro_high_vol_chop(n: int = 260) -> pd.DataFrame:
    # Large daily swings around a flat mean: no sustained trend, but daily
    # returns big enough that rolling annualised volatility clears 20%.
    rng = np.random.default_rng(3)
    daily_returns = rng.normal(0, 0.03, n)
    close = 100 * np.cumprod(1 + daily_returns)
    return _macro_ohlc(close)


def _macro_neutral(n: int = 260) -> pd.DataFrame:
    # Flat, low-volatility chop around a constant mean: SMAs converge near
    # each other and price oscillates through them, so neither trend
    # condition holds and volatility stays well under the threshold.
    rng = np.random.default_rng(4)
    close = 100 + np.sin(np.linspace(0, 6 * np.pi, n)) * 0.5 + rng.normal(0, 0.02, n)
    return _macro_ohlc(close)


@pytest.fixture
def macro_detector():
    return MacroRegimeDetector(
        sma_fast_period=50,
        sma_slow_period=200,
        volatility_period=20,
        high_vol_annualized_threshold=0.20,
    )


class TestMacroRegimeConstruction:
    def test_rejects_short_fast_period(self):
        with pytest.raises(ValueError):
            MacroRegimeDetector(sma_fast_period=1)

    def test_rejects_slow_period_not_greater_than_fast(self):
        with pytest.raises(ValueError):
            MacroRegimeDetector(sma_fast_period=50, sma_slow_period=50)
        with pytest.raises(ValueError):
            MacroRegimeDetector(sma_fast_period=50, sma_slow_period=20)

    def test_rejects_short_volatility_period(self):
        with pytest.raises(ValueError):
            MacroRegimeDetector(volatility_period=1)

    def test_rejects_non_positive_threshold(self):
        with pytest.raises(ValueError):
            MacroRegimeDetector(high_vol_annualized_threshold=0)


class TestMacroComputeIndicators:
    def test_adds_expected_columns(self, macro_detector):
        out = macro_detector.compute_indicators(_macro_bull())
        for col in ("sma_fast", "sma_slow", "volatility_annualized"):
            assert col in out.columns

    def test_warmup_rows_are_nan(self, macro_detector):
        out = macro_detector.compute_indicators(_macro_bull())
        assert out["sma_slow"].iloc[:199].isna().all()
        assert pd.notna(out["sma_slow"].iloc[199])

    def test_rejects_empty_frame(self, macro_detector):
        with pytest.raises(ValueError, match="empty"):
            macro_detector.compute_indicators(_macro_ohlc(np.array([])))

    def test_rejects_missing_close(self, macro_detector):
        df = _macro_bull().drop(columns=["Close"])
        with pytest.raises(ValueError, match="missing required column"):
            macro_detector.compute_indicators(df)


class TestMacroDetectRegime:
    def test_sustained_uptrend_is_bull_trending(self, macro_detector):
        assert macro_detector.current_regime(_macro_bull()) == MACRO_BULL_TRENDING

    def test_sustained_downtrend_is_bear_trending(self, macro_detector):
        assert macro_detector.current_regime(_macro_bear()) == MACRO_BEAR_TRENDING

    def test_large_daily_swings_are_high_volatility_chop(self, macro_detector):
        assert (
            macro_detector.current_regime(_macro_high_vol_chop())
            == MACRO_HIGH_VOLATILITY_CHOP
        )

    def test_flat_low_vol_chop_is_neutral(self, macro_detector):
        assert macro_detector.current_regime(_macro_neutral()) == MACRO_NEUTRAL

    def test_volatility_takes_priority_over_trend(self, macro_detector):
        # A strong uptrend whose recent volatility has spiked must still
        # read as HIGH_VOLATILITY_CHOP, not BULL_TRENDING - the whole point
        # of the volatility gate is that it overrides a trend read.
        rng = np.random.default_rng(5)
        n = 260
        trend = 100 + 0.3 * np.arange(n)
        spike = rng.normal(0, 0.06, n)
        spike[-20:] += rng.normal(0, 3.0, 20)  # volatility spike at the tail
        close = trend + spike
        assert (
            macro_detector.current_regime(_macro_ohlc(close))
            == MACRO_HIGH_VOLATILITY_CHOP
        )

    def test_warmup_rows_are_none(self, macro_detector):
        regime = macro_detector.detect_regime(_macro_bull())
        assert regime.iloc[0] is None

    def test_insufficient_data_returns_unknown(self, macro_detector):
        assert macro_detector.current_regime(_macro_bull(n=50)) == MACRO_REGIME_UNKNOWN


class TestMacroIsLongBlocked:
    def test_bull_trending_is_not_blocked(self, macro_detector):
        assert macro_detector.is_long_blocked(_macro_bull()) is False

    def test_bear_trending_is_blocked(self, macro_detector):
        assert macro_detector.is_long_blocked(_macro_bear()) is True

    def test_high_volatility_chop_is_blocked(self, macro_detector):
        assert macro_detector.is_long_blocked(_macro_high_vol_chop()) is True

    def test_neutral_is_not_blocked(self, macro_detector):
        assert macro_detector.is_long_blocked(_macro_neutral()) is False

    def test_unknown_is_not_blocked(self, macro_detector):
        assert macro_detector.is_long_blocked(_macro_bull(n=50)) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
