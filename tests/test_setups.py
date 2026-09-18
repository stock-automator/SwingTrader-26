"""
Tests for `backend.app.quant.setups`: latest-bar setup resolution and the
categorized skip reasons `scan_universe` reports for a scanned universe.
"""

import pandas as pd
import pytest

from backend.app.quant.risk import RiskManager
from backend.app.quant.setups import (
    MIN_BARS,
    DataStaleError,
    InsufficientHistoryError,
    ScanSkipError,
    VolumeFilterFailedError,
    ZeroLiquidityError,
    scan_ticker,
    scan_universe,
)
from backend.app.quant.strategies.donchian_breakout import DonchianBreakout


@pytest.fixture
def risk_manager() -> RiskManager:
    return RiskManager(account_equity=10_000.0, risk_per_trade_pct=0.02)


@pytest.fixture
def strategy() -> DonchianBreakout:
    return DonchianBreakout()


class TestScanTickerSkipCategories:
    def test_insufficient_history_raises_with_reason(
        self, strategy, risk_manager, ohlcv
    ):
        short_df = ohlcv.iloc[: MIN_BARS - 1]
        with pytest.raises(InsufficientHistoryError) as exc_info:
            scan_ticker("AAPL", short_df, strategy, risk_manager)
        assert exc_info.value.reason == "INSUFFICIENT_HISTORY"

    def test_zero_latest_volume_raises_zero_liquidity(
        self, strategy, risk_manager, ohlcv
    ):
        df = ohlcv.copy()
        df.loc[df.index[-1], "Volume"] = 0
        with pytest.raises(ZeroLiquidityError) as exc_info:
            scan_ticker("AAPL", df, strategy, risk_manager)
        assert exc_info.value.reason == "ZERO_LIQUIDITY"

    def test_low_average_volume_raises_volume_filter_failed(
        self, strategy, risk_manager, ohlcv
    ):
        df = ohlcv.copy()
        df["Volume"] = 100.0  # far below the default 100,000-share floor
        with pytest.raises(VolumeFilterFailedError) as exc_info:
            scan_ticker("AAPL", df, strategy, risk_manager, min_avg_volume=100_000.0)
        assert exc_info.value.reason == "VOLUME_FILTER_FAILED"

    def test_min_avg_volume_none_disables_the_floor_but_not_zero_check(
        self, strategy, risk_manager, ohlcv
    ):
        df = ohlcv.copy()
        df["Volume"] = 1.0  # would fail the floor, but it's disabled
        # Should not raise VolumeFilterFailedError.
        scan_ticker("AAPL", df, strategy, risk_manager, min_avg_volume=None)

    def test_stale_data_raises_when_enabled(self, strategy, risk_manager, ohlcv):
        as_of = pd.Timestamp(ohlcv.index[-1]) + pd.Timedelta(days=30)
        with pytest.raises(DataStaleError) as exc_info:
            scan_ticker(
                "AAPL",
                ohlcv,
                strategy,
                risk_manager,
                stale_after_days=5,
                as_of_reference=as_of,
            )
        assert exc_info.value.reason == "DATA_STALE"

    def test_stale_check_disabled_by_default(self, strategy, risk_manager, ohlcv):
        # ohlcv's fixed 2023 dates are always "stale" relative to wall-clock
        # time; the default (stale_after_days=None) must not reject it.
        scan_ticker("AAPL", ohlcv, strategy, risk_manager)

    def test_all_skip_errors_are_valueerror_subclasses(self):
        for cls in (
            InsufficientHistoryError,
            DataStaleError,
            VolumeFilterFailedError,
            ZeroLiquidityError,
        ):
            assert issubclass(cls, ScanSkipError)
            assert issubclass(cls, ValueError)


class TestScanUniverseSkipReasons:
    def test_categorizes_mixed_universe_by_skip_reason(
        self, strategy, risk_manager, ohlcv
    ):
        zero_liquidity_df = ohlcv.copy()
        zero_liquidity_df.loc[zero_liquidity_df.index[-1], "Volume"] = 0

        low_volume_df = ohlcv.copy()
        low_volume_df["Volume"] = 100.0

        frames = {
            "GOOD": ohlcv,
            "SHORT": ohlcv.iloc[: MIN_BARS - 1],
            "ILLIQUID": zero_liquidity_df,
            "THIN": low_volume_df,
        }

        report = scan_universe(frames, strategy, risk_manager)

        assert report.skip_reasons["INSUFFICIENT_HISTORY"] == 1
        assert report.skip_reasons["ZERO_LIQUIDITY"] == 1
        assert report.skip_reasons["VOLUME_FILTER_FAILED"] == 1
        # GOOD is a steady uptrend with no signal on the final bar for this
        # strategy config - reported as the pre-existing "no_signal" bucket.
        assert report.skipped == 4
        assert report.skip_reasons["no_signal"] == 1
