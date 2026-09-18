"""
Tests for quant/gating.py: regime and earnings-blackout strategy wrappers.
"""

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.gating import EarningsGatedStrategy, RegimeGatedStrategy
from backend.app.quant.regime import MacroRegimeDetector
from backend.app.quant.screener import CatalystFilter
from backend.app.quant.strategies.base import BaseStrategy


class _AlwaysBuyStrategy(BaseStrategy):
    """Fires a BUY signal on every bar after a short warm-up - a stand-in
    inner strategy that makes the wrapper's own suppression logic the only
    thing under test."""

    def __init__(self, warm_up: int = 5):
        super().__init__(name="Always Buy")
        self.warm_up = warm_up

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        warm_up = self.warm_up
        signal = pd.Series(0, index=df.index, dtype=int)
        signal.iloc[warm_up:] = 1

        out = df.copy()
        out["signal"] = signal
        out["sl_type"] = np.where(signal != 0, "PERCENTAGE", None)
        out["sl_value"] = np.where(signal != 0, 0.02, np.nan)
        out["tp_type"] = np.where(signal != 0, "PERCENTAGE", None)
        out["tp_value"] = np.where(signal != 0, 0.05, np.nan)
        return out


def _ohlc(close: np.ndarray, start: str = "2020-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(close), freq="D")
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.2,
            "Low": close - 0.2,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=idx,
    ).astype(
        {"Open": float, "High": float, "Low": float, "Close": float, "Volume": float}
    )


class TestRegimeGatedStrategy:
    def test_suppresses_longs_in_a_bear_trending_benchmark(self):
        n = 260
        # Steady downtrend benchmark -> BEAR_TRENDING throughout the tail.
        benchmark = _ohlc(200 - 0.3 * np.arange(n, dtype=float))
        asset = _ohlc(100 + np.arange(n, dtype=float) * 0.01)

        gated = RegimeGatedStrategy(_AlwaysBuyStrategy(), benchmark_df=benchmark)
        out = gated.generate_signals(asset)

        # Once the macro regime is classified (after its own warm-up), every
        # bar should have its long suppressed.
        detector = MacroRegimeDetector()
        regime = detector.detect_regime(benchmark)
        classified = regime.notna()
        assert (out.loc[classified, "signal"] == 0).all()

    def test_passes_through_longs_in_a_bull_trending_benchmark(self):
        n = 260
        benchmark = _ohlc(100 + 0.3 * np.arange(n, dtype=float))
        asset = _ohlc(100 + np.arange(n, dtype=float) * 0.01)

        gated = RegimeGatedStrategy(_AlwaysBuyStrategy(), benchmark_df=benchmark)
        out = gated.generate_signals(asset)
        inner = _AlwaysBuyStrategy().generate_signals(asset)

        pd.testing.assert_series_equal(out["signal"], inner["signal"])

    def test_wrapped_name_reflects_the_inner_strategy(self):
        gated = RegimeGatedStrategy(
            _AlwaysBuyStrategy(), benchmark_df=_ohlc(np.full(50, 100.0))
        )
        assert "Always Buy" in gated.name
        assert "regime-gated" in gated.name

    def test_output_still_satisfies_base_strategy_contract(self):
        n = 260
        benchmark = _ohlc(200 - 0.3 * np.arange(n, dtype=float))
        asset = _ohlc(100 + np.arange(n, dtype=float) * 0.01)

        gated = RegimeGatedStrategy(_AlwaysBuyStrategy(), benchmark_df=benchmark)
        out = gated.generate_signals(asset)
        BaseStrategy.validate_output(out)


class TestEarningsGatedStrategy:
    def test_empty_earnings_list_is_a_no_op(self):
        asset = _ohlc(100 + np.arange(50, dtype=float))
        gated = EarningsGatedStrategy(_AlwaysBuyStrategy(), earnings_dates=[])
        out = gated.generate_signals(asset)
        inner = _AlwaysBuyStrategy().generate_signals(asset)

        pd.testing.assert_series_equal(out["signal"], inner["signal"])

    def test_suppresses_longs_inside_the_blackout_window(self):
        asset = _ohlc(100 + np.arange(50, dtype=float))
        earnings_date = asset.index[30]  # a Wednesday-ish mid-series date

        gated = EarningsGatedStrategy(
            _AlwaysBuyStrategy(),
            earnings_dates=[earnings_date],
            catalyst_filter=CatalystFilter(blackout_days=5),
        )
        out = gated.generate_signals(asset)

        # The exact earnings-day bar itself must be suppressed.
        assert out.loc[earnings_date, "signal"] == 0

    def test_leaves_bars_well_outside_the_window_untouched(self):
        asset = _ohlc(100 + np.arange(50, dtype=float))
        earnings_date = asset.index[30]

        gated = EarningsGatedStrategy(
            _AlwaysBuyStrategy(),
            earnings_dates=[earnings_date],
            catalyst_filter=CatalystFilter(blackout_days=5),
        )
        out = gated.generate_signals(asset)

        far_before = asset.index[5]
        assert out.loc[far_before, "signal"] == 1

    def test_output_still_satisfies_base_strategy_contract(self):
        asset = _ohlc(100 + np.arange(50, dtype=float))
        gated = EarningsGatedStrategy(
            _AlwaysBuyStrategy(), earnings_dates=[asset.index[30]]
        )
        out = gated.generate_signals(asset)
        BaseStrategy.validate_output(out)

    def test_wrapped_name_reflects_the_inner_strategy(self):
        gated = EarningsGatedStrategy(_AlwaysBuyStrategy(), earnings_dates=[])
        assert "Always Buy" in gated.name
        assert "earnings-gated" in gated.name

    def test_per_ticker_map_blocks_only_the_tagged_ticker(self):
        # Two sleeves sharing one strategy instance (`run_comparison`'s
        # design) - only the frame tagged "AAPL" carries an earnings date in
        # the blackout window.
        aapl = _ohlc(100 + np.arange(50, dtype=float))
        aapl.attrs["ticker"] = "AAPL"
        msft = _ohlc(100 + np.arange(50, dtype=float))
        msft.attrs["ticker"] = "MSFT"

        gated = EarningsGatedStrategy(
            _AlwaysBuyStrategy(),
            earnings_by_ticker={"AAPL": [aapl.index[30]]},
            catalyst_filter=CatalystFilter(blackout_days=5),
        )

        aapl_out = gated.generate_signals(aapl)
        msft_out = gated.generate_signals(msft)

        assert aapl_out.loc[aapl.index[30], "signal"] == 0
        assert msft_out.loc[msft.index[30], "signal"] == 1

    def test_untagged_frame_falls_back_to_flat_earnings_dates(self):
        asset = _ohlc(100 + np.arange(50, dtype=float))  # no .attrs["ticker"]
        earnings_date = asset.index[30]

        gated = EarningsGatedStrategy(
            _AlwaysBuyStrategy(),
            earnings_dates=[earnings_date],
            earnings_by_ticker={"AAPL": []},
            catalyst_filter=CatalystFilter(blackout_days=5),
        )
        out = gated.generate_signals(asset)
        assert out.loc[earnings_date, "signal"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
