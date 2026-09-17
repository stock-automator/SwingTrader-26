"""
Tests for the relative-strength screener.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.screener import RelativeStrengthScreener, relative_strength


def _ohlc_from_closes(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="D")
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=idx,
    )


class TestRelativeStrength:
    def test_known_outperformance(self):
        # Asset +20% over 10 bars, benchmark +10% -> relative strength +10pp.
        asset = _ohlc_from_closes([100.0] * 1 + [100.0] + [120.0] * 9)
        benchmark = _ohlc_from_closes([100.0] * 1 + [100.0] + [110.0] * 9)
        rs = relative_strength(asset, benchmark, lookback=10)
        assert rs == pytest.approx(0.10, abs=1e-6)

    def test_rejects_insufficient_asset_history(self):
        asset = _ohlc_from_closes([100.0] * 5)
        benchmark = _ohlc_from_closes([100.0] * 20)
        with pytest.raises(ValueError):
            relative_strength(asset, benchmark, lookback=10)

    def test_rejects_insufficient_benchmark_history(self):
        asset = _ohlc_from_closes([100.0] * 20)
        benchmark = _ohlc_from_closes([100.0] * 5)
        with pytest.raises(ValueError):
            relative_strength(asset, benchmark, lookback=10)

    def test_rejects_non_positive_lookback(self):
        asset = _ohlc_from_closes([100.0] * 20)
        benchmark = _ohlc_from_closes([100.0] * 20)
        with pytest.raises(ValueError):
            relative_strength(asset, benchmark, lookback=0)


class TestRelativeStrengthScreener:
    def test_ranks_strongest_first(self):
        benchmark = _ohlc_from_closes([100.0] + [100.0] * 62 + [110.0])
        universe = {
            "WEAK": _ohlc_from_closes([100.0] + [100.0] * 62 + [102.0]),
            "STRONG": _ohlc_from_closes([100.0] + [100.0] * 62 + [130.0]),
            "MID": _ohlc_from_closes([100.0] + [100.0] * 62 + [115.0]),
        }
        screener = RelativeStrengthScreener(benchmark, lookback=63)
        ranked = screener.rank(universe)

        assert list(ranked["ticker"]) == ["STRONG", "MID", "WEAK"]
        assert list(ranked["rank"]) == [1, 2, 3]

    def test_drops_tickers_with_insufficient_history(self):
        benchmark = _ohlc_from_closes([100.0] * 70)
        universe = {
            "TOO_SHORT": _ohlc_from_closes([100.0] * 5),
            "OK": _ohlc_from_closes([100.0] * 70),
        }
        screener = RelativeStrengthScreener(benchmark, lookback=63)
        ranked = screener.rank(universe)

        assert list(ranked["ticker"]) == ["OK"]

    def test_rejects_non_positive_lookback(self):
        benchmark = _ohlc_from_closes([100.0] * 70)
        with pytest.raises(ValueError):
            RelativeStrengthScreener(benchmark, lookback=0)

    def test_rejects_insufficient_benchmark_history(self):
        # An under-length benchmark would invalidate every score, so this
        # raises rather than silently dropping tickers.
        benchmark = _ohlc_from_closes([100.0] * 10)
        screener = RelativeStrengthScreener(benchmark, lookback=63)
        with pytest.raises(ValueError):
            screener.rank({"OK": _ohlc_from_closes([100.0] * 70)})

    def test_matches_standalone_function(self):
        # The class and the module-level function must agree on the math.
        benchmark = _ohlc_from_closes([100.0] + [100.0] * 62 + [110.0])
        asset = _ohlc_from_closes([100.0] + [100.0] * 62 + [130.0])
        screener = RelativeStrengthScreener(benchmark, lookback=63)
        ranked = screener.rank({"STRONG": asset})

        assert ranked.loc[0, "relative_strength"] == pytest.approx(
            relative_strength(asset, benchmark, lookback=63)
        )

    def test_empty_universe_returns_empty_frame(self):
        benchmark = _ohlc_from_closes([100.0] * 70)
        screener = RelativeStrengthScreener(benchmark, lookback=63)
        ranked = screener.rank({})

        assert ranked.empty
        assert list(ranked.columns) == [
            "ticker",
            "asset_return",
            "benchmark_return",
            "relative_strength",
            "rank",
        ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
