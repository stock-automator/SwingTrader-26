"""
Tests for the relative-strength screener.
"""

import pandas as pd
import pytest

from backend.app.quant.screener import (
    CatalystFilter,
    RelativeStrengthScreener,
    relative_strength,
)


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


def _window_closes(
    anchor: float, latest: float, lookback: int, filler: float = 55.0
) -> list[float]:
    """Closes of length `lookback + 1` with both window endpoints pinned.

    Element 0 is the anchor the return must be measured from, element -1 is
    the latest bar, and everything between is `filler` - a price equal to
    *neither* endpoint. That middle value is the point: it makes an
    off-by-one in the anchor index (`iloc[-lookback - 1]` vs
    `iloc[-lookback]`) change the computed return instead of silently
    agreeing with it, which is what happens when the filler is set equal to
    the anchor.
    """
    return [anchor] + [filler] * (lookback - 1) + [latest]


class TestRelativeStrength:
    def test_known_outperformance(self):
        # Asset +20% over 10 bars, benchmark +10% -> relative strength +10pp.
        asset = _ohlc_from_closes(_window_closes(100.0, 120.0, lookback=10))
        benchmark = _ohlc_from_closes(_window_closes(100.0, 110.0, lookback=10))
        rs = relative_strength(asset, benchmark, lookback=10)
        assert rs == pytest.approx(0.10, abs=1e-6)

    def test_measures_from_the_bar_before_the_window(self):
        # Pins the anchor index itself. The window is the trailing `lookback`
        # bars, so a 10-bar return anchors on iloc[-11], not iloc[-10]. With
        # the filler at 55.0, anchoring one bar late would give 120/55 - 1 =
        # 1.18 instead of 0.20, so this test fails loudly on that mutation.
        asset = _ohlc_from_closes(_window_closes(100.0, 120.0, lookback=10))
        flat_benchmark = _ohlc_from_closes([100.0] * 11)

        assert relative_strength(asset, flat_benchmark, lookback=10) == pytest.approx(
            0.20, abs=1e-6
        )

    def test_shorter_lookback_sees_a_different_anchor(self):
        # Same frame, two lookbacks, two different anchors -> two different
        # returns. Guards against a lookback argument that is accepted but
        # not actually used in the indexing.
        closes = [10.0, 20.0, 40.0, 80.0, 160.0]
        asset = _ohlc_from_closes(closes)
        flat = _ohlc_from_closes([100.0] * 5)

        # lookback=1 anchors on 80.0 -> +100%; lookback=4 anchors on 10.0 -> +1500%.
        assert relative_strength(asset, flat, lookback=1) == pytest.approx(1.0)
        assert relative_strength(asset, flat, lookback=4) == pytest.approx(15.0)

    def test_rejects_zero_close_at_anchor(self):
        # A halted or gap-filled symbol can carry a 0.0 close; float division
        # yields inf rather than raising, so this must be caught explicitly.
        asset = _ohlc_from_closes(_window_closes(0.0, 120.0, lookback=10))
        benchmark = _ohlc_from_closes([100.0] * 11)
        with pytest.raises(ValueError, match="anchor close must be positive"):
            relative_strength(asset, benchmark, lookback=10)

    def test_rejects_nan_close_at_latest_bar(self):
        asset = _ohlc_from_closes(_window_closes(100.0, float("nan"), lookback=10))
        benchmark = _ohlc_from_closes([100.0] * 11)
        with pytest.raises(ValueError, match="latest close must be positive"):
            relative_strength(asset, benchmark, lookback=10)

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
        benchmark = _ohlc_from_closes(_window_closes(100.0, 110.0, lookback=63))
        universe = {
            "WEAK": _ohlc_from_closes(_window_closes(100.0, 102.0, lookback=63)),
            "STRONG": _ohlc_from_closes(_window_closes(100.0, 130.0, lookback=63)),
            "MID": _ohlc_from_closes(_window_closes(100.0, 115.0, lookback=63)),
        }
        screener = RelativeStrengthScreener(benchmark, lookback=63)
        ranked = screener.rank(universe)

        assert list(ranked["ticker"]) == ["STRONG", "MID", "WEAK"]
        assert list(ranked["rank"]) == [1, 2, 3]

    def test_scores_are_relative_to_the_benchmark(self):
        # Both names rose, but the benchmark rose more than one of them - so
        # that name's relative strength must go negative. A screener that
        # scored raw returns would report both as positive.
        benchmark = _ohlc_from_closes(_window_closes(100.0, 120.0, lookback=63))
        universe = {
            "LAGGARD": _ohlc_from_closes(_window_closes(100.0, 110.0, lookback=63)),
            "LEADER": _ohlc_from_closes(_window_closes(100.0, 140.0, lookback=63)),
        }
        ranked = RelativeStrengthScreener(benchmark, lookback=63).rank(universe)
        scores = ranked.set_index("ticker")["relative_strength"]

        assert scores["LEADER"] == pytest.approx(0.20, abs=1e-6)
        assert scores["LAGGARD"] == pytest.approx(-0.10, abs=1e-6)
        assert list(ranked["benchmark_return"]) == pytest.approx([0.20, 0.20])

    def test_drops_tickers_with_unscoreable_prices(self):
        # Zero and NaN closes are dropped like an under-length ticker. If they
        # survived, their inf/NaN scores would sort to rank 1 - the worst
        # possible failure for something whose output picks what to trade.
        benchmark = _ohlc_from_closes(_window_closes(100.0, 110.0, lookback=63))
        universe = {
            "ZERO_ANCHOR": _ohlc_from_closes(_window_closes(0.0, 120.0, lookback=63)),
            "NAN_LATEST": _ohlc_from_closes(
                _window_closes(100.0, float("nan"), lookback=63)
            ),
            "OK": _ohlc_from_closes(_window_closes(100.0, 130.0, lookback=63)),
        }
        ranked = RelativeStrengthScreener(benchmark, lookback=63).rank(universe)

        assert list(ranked["ticker"]) == ["OK"]

    def test_rejects_unscoreable_benchmark(self):
        # A bad benchmark invalidates every score, so unlike a bad ticker it
        # cannot be dropped - it has to raise.
        benchmark = _ohlc_from_closes(_window_closes(0.0, 110.0, lookback=63))
        screener = RelativeStrengthScreener(benchmark, lookback=63)
        with pytest.raises(ValueError, match="close must be positive"):
            screener.rank({"OK": _ohlc_from_closes([100.0] * 70)})

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
        benchmark = _ohlc_from_closes(_window_closes(100.0, 110.0, lookback=63))
        asset = _ohlc_from_closes(_window_closes(100.0, 130.0, lookback=63))
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


class TestCatalystFilter:
    def test_rejects_negative_blackout_days(self):
        with pytest.raises(ValueError):
            CatalystFilter(blackout_days=-1)

    def test_no_upcoming_earnings_returns_none_distance(self):
        cf = CatalystFilter(blackout_days=5)
        as_of = pd.Timestamp("2024-06-10")  # a Monday
        past_earnings = [pd.Timestamp("2024-05-01")]
        assert cf.trading_days_to_next_earnings(as_of, past_earnings) is None
        assert cf.is_blocked(as_of, past_earnings) is False

    def test_earnings_today_is_zero_distance_and_blocked(self):
        cf = CatalystFilter(blackout_days=5)
        as_of = pd.Timestamp("2024-06-10")
        assert cf.trading_days_to_next_earnings(as_of, [as_of]) == 0
        assert cf.is_blocked(as_of, [as_of]) is True

    def test_one_week_out_five_business_days_is_blocked(self):
        # Monday -> next Monday is 5 business days (Tue,Wed,Thu,Fri,Mon).
        cf = CatalystFilter(blackout_days=5)
        as_of = pd.Timestamp("2024-06-10")  # Monday
        earnings = pd.Timestamp("2024-06-17")  # following Monday
        assert cf.trading_days_to_next_earnings(as_of, [earnings]) == 5
        assert cf.is_blocked(as_of, [earnings]) is True

    def test_six_business_days_out_is_not_blocked(self):
        cf = CatalystFilter(blackout_days=5)
        as_of = pd.Timestamp("2024-06-10")  # Monday
        earnings = pd.Timestamp("2024-06-18")  # Tuesday, 6 business days out
        assert cf.trading_days_to_next_earnings(as_of, [earnings]) == 6
        assert cf.is_blocked(as_of, [earnings]) is False

    def test_picks_the_nearest_upcoming_date_among_several(self):
        cf = CatalystFilter(blackout_days=5)
        as_of = pd.Timestamp("2024-06-10")
        earnings = [
            pd.Timestamp("2024-01-01"),  # past, ignored
            pd.Timestamp("2024-09-01"),  # far future
            pd.Timestamp("2024-06-12"),  # nearest upcoming
        ]
        assert cf.trading_days_to_next_earnings(as_of, earnings) == 2

    def test_empty_earnings_list_is_never_blocked(self):
        cf = CatalystFilter(blackout_days=5)
        assert cf.is_blocked(pd.Timestamp("2024-06-10"), []) is False

    def test_zero_blackout_days_only_blocks_earnings_day_itself(self):
        cf = CatalystFilter(blackout_days=0)
        as_of = pd.Timestamp("2024-06-10")
        assert cf.is_blocked(as_of, [as_of]) is True
        assert cf.is_blocked(as_of, [pd.Timestamp("2024-06-11")]) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
