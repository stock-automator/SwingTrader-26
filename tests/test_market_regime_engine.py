"""
Tests for `MarketRegimeEngine`: SPY/QQQ EMA alignment, VIX volatility
regime, S&P 500 breadth, and the combined `MarketHealthReport` traffic
light (`BULL_CONFIRMED` / `CAUTION_CHOP` / `BEAR_DEFENSIVE`).
"""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.regime import (
    EMA_ALIGNMENT_BEARISH,
    EMA_ALIGNMENT_BULLISH,
    EMA_ALIGNMENT_NEUTRAL,
    EMA_ALIGNMENT_UNKNOWN,
    HEALTH_BEAR_DEFENSIVE,
    HEALTH_BULL_CONFIRMED,
    HEALTH_CAUTION_CHOP,
    VIX_EXTREME,
    VIX_HIGH,
    VIX_LOW,
    VIX_NORMAL,
    VIX_UNKNOWN,
    BreadthResult,
    MarketRegimeEngine,
)


def _trending_frame(start: float, drift: float, n: int = 260) -> pd.DataFrame:
    """Monotonic-ish price path so the EMA(50)/EMA(200) alignment is
    unambiguous, e.g. `drift=1.0` for a strong uptrend, `-1.0` for a
    downtrend."""
    close = start + drift * np.arange(n)
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({"Close": close}, index=idx)


def _flat_frame(level: float = 100.0, n: int = 260) -> pd.DataFrame:
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({"Close": [level] * n}, index=idx)


@pytest.fixture
def engine() -> MarketRegimeEngine:
    return MarketRegimeEngine()


class TestConstruction:
    def test_rejects_bad_ema_periods(self):
        with pytest.raises(ValueError):
            MarketRegimeEngine(ema_fast_period=1)
        with pytest.raises(ValueError):
            MarketRegimeEngine(ema_fast_period=50, ema_slow_period=50)

    def test_rejects_bad_breadth_thresholds(self):
        with pytest.raises(ValueError):
            MarketRegimeEngine(breadth_bull_threshold=40, breadth_bear_threshold=60)

    def test_rejects_bad_vix_breakpoints(self):
        with pytest.raises(ValueError):
            MarketRegimeEngine(vix_low_max=20, vix_normal_max=15)


class TestEmaAlignment:
    def test_uptrend_is_bullish(self, engine):
        assert engine.ema_alignment(_trending_frame(100, 1.0)) == EMA_ALIGNMENT_BULLISH

    def test_downtrend_is_bearish(self, engine):
        assert engine.ema_alignment(_trending_frame(500, -1.0)) == EMA_ALIGNMENT_BEARISH

    def test_flat_price_is_neutral(self, engine):
        assert engine.ema_alignment(_flat_frame()) == EMA_ALIGNMENT_NEUTRAL

    def test_insufficient_history_is_unknown(self, engine):
        assert engine.ema_alignment(_flat_frame(n=10)) == EMA_ALIGNMENT_UNKNOWN

    def test_empty_frame_is_unknown(self, engine):
        assert engine.ema_alignment(pd.DataFrame()) == EMA_ALIGNMENT_UNKNOWN

    def test_none_frame_is_unknown(self, engine):
        assert engine.ema_alignment(None) == EMA_ALIGNMENT_UNKNOWN


class TestVixRegime:
    def test_buckets(self, engine):
        assert engine.vix_regime(12.0) == VIX_LOW
        assert engine.vix_regime(17.5) == VIX_NORMAL
        assert engine.vix_regime(25.0) == VIX_HIGH
        assert engine.vix_regime(35.0) == VIX_EXTREME

    def test_none_is_unknown(self, engine):
        assert engine.vix_regime(None) == VIX_UNKNOWN

    def test_nan_is_unknown(self, engine):
        assert engine.vix_regime(float("nan")) == VIX_UNKNOWN

    def test_boundaries_are_exclusive_upper(self, engine):
        assert engine.vix_regime(15.0) == VIX_NORMAL
        assert engine.vix_regime(20.0) == VIX_HIGH
        assert engine.vix_regime(30.0) == VIX_EXTREME


class TestComputeBreadth:
    def test_mixed_universe_scores_correctly(self, engine):
        frames = {
            "UP1": _trending_frame(100, 1.0),
            "UP2": _trending_frame(50, 0.5),
            "DOWN1": _trending_frame(500, -1.0),
            "TOO_SHORT": _flat_frame(n=5),
        }
        result = engine.compute_breadth(frames)
        assert result.above == 2
        assert result.below == 1
        assert result.unscored == 1
        assert result.total_scored == 3
        assert result.pct_above_50ema == pytest.approx(200 / 3, rel=1e-6)

    def test_empty_universe(self, engine):
        result = engine.compute_breadth({})
        assert result.pct_above_50ema == 0.0
        assert result.total_scored == 0

    def test_all_unscored_gives_zero_pct_not_error(self, engine):
        frames = {"A": _flat_frame(n=5), "B": _flat_frame(n=5)}
        result = engine.compute_breadth(frames)
        assert result.total_scored == 0
        assert result.pct_above_50ema == 0.0


class TestClassify:
    def test_bull_confirmed(self, engine):
        report = engine.classify(
            spy_df=_trending_frame(100, 1.0),
            qqq_df=_trending_frame(100, 1.0),
            vix_level=13.0,
            breadth=BreadthResult(pct_above_50ema=75.0, above=75, below=25, unscored=0),
        )
        assert report.state == HEALTH_BULL_CONFIRMED
        assert report.vix_regime == VIX_LOW

    def test_bear_defensive_on_bearish_alignment(self, engine):
        report = engine.classify(
            spy_df=_trending_frame(500, -1.0),
            qqq_df=_trending_frame(100, 1.0),
            vix_level=13.0,
            breadth=BreadthResult(pct_above_50ema=75.0, above=75, below=25, unscored=0),
        )
        assert report.state == HEALTH_BEAR_DEFENSIVE

    def test_bear_defensive_on_extreme_vix_overrides_bullish_trend(self, engine):
        report = engine.classify(
            spy_df=_trending_frame(100, 1.0),
            qqq_df=_trending_frame(100, 1.0),
            vix_level=40.0,
            breadth=BreadthResult(pct_above_50ema=80.0, above=80, below=20, unscored=0),
        )
        assert report.state == HEALTH_BEAR_DEFENSIVE

    def test_bear_defensive_on_weak_breadth(self, engine):
        report = engine.classify(
            spy_df=_trending_frame(100, 1.0),
            qqq_df=_trending_frame(100, 1.0),
            vix_level=13.0,
            breadth=BreadthResult(pct_above_50ema=30.0, above=30, below=70, unscored=0),
        )
        assert report.state == HEALTH_BEAR_DEFENSIVE

    def test_caution_chop_on_mixed_signals(self, engine):
        report = engine.classify(
            spy_df=_flat_frame(),
            qqq_df=_flat_frame(),
            vix_level=17.0,
            breadth=BreadthResult(pct_above_50ema=50.0, above=50, below=50, unscored=0),
        )
        assert report.state == HEALTH_CAUTION_CHOP

    def test_missing_breadth_and_vix_falls_back_to_chop_not_crash(self, engine):
        report = engine.classify(
            spy_df=_flat_frame(), qqq_df=_flat_frame(), vix_level=None, breadth=None
        )
        assert report.state == HEALTH_CAUTION_CHOP
        assert report.vix_regime == VIX_UNKNOWN
        assert report.breadth_pct is None

    def test_report_as_dict_serializable(self, engine):
        report = engine.classify(
            spy_df=_trending_frame(100, 1.0),
            qqq_df=_trending_frame(100, 1.0),
            vix_level=13.0,
            breadth=BreadthResult(pct_above_50ema=75.0, above=75, below=25, unscored=0),
        )
        payload = report.as_dict()
        assert payload["state"] == HEALTH_BULL_CONFIRMED
        assert isinstance(payload["notes"], list)
