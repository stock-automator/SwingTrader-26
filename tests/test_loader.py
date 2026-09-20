"""
Tests for `backend.app.data.loader`'s frame-normalization helpers, focused
on the delisted/malformed-ticker robustness fixes: a duplicate-labeled
MultiIndex (the shape a delisted/aliased symbol can come back as from
yfinance) must not survive into `normalize_ohlcv`'s column selection, where
it would otherwise raise pandas' "Columns must be same length as key".
"""

import pandas as pd
import pytest

from backend.app.data.loader import (
    DataUnavailableError,
    apply_split_dividend_adjustment,
    fetch_yfinance,
    flatten_columns,
    normalize_ohlcv,
)


def _multiindex_frame(ticker: str = "BK", n: int = 5) -> pd.DataFrame:
    index = pd.date_range("2023-01-02", periods=n, freq="B")
    columns = pd.MultiIndex.from_product(
        [["Open", "High", "Low", "Close", "Volume"], [ticker]]
    )
    data = {
        (field, ticker): [100.0 + i for i in range(n)]
        for field in ["Open", "High", "Low", "Close", "Volume"]
    }
    return pd.DataFrame(data, index=index, columns=columns)


class TestFlattenColumnsDedup:
    def test_single_level_columns_pass_through_unchanged(self):
        df = pd.DataFrame({"Close": [1.0, 2.0]})
        result = flatten_columns(df)
        assert list(result.columns) == ["Close"]

    def test_duplicate_labels_after_droplevel_are_deduped(self):
        """A delisted/aliased ticker can make yfinance return a MultiIndex
        whose ticker-level values differ (e.g. an old vs. new symbol) but
        whose field level collides once dropped - simulated here by
        concatenating two same-field column blocks."""
        index = pd.date_range("2023-01-02", periods=3, freq="B")
        columns = pd.MultiIndex.from_tuples(
            [
                ("Close", "BK"),
                ("Close", "BK-OLD"),
                ("Open", "BK"),
                ("High", "BK"),
                ("Low", "BK"),
                ("Volume", "BK"),
            ]
        )
        df = pd.DataFrame(
            [[1.0, 1.1, 2.0, 3.0, 0.5, 1000]] * 3, index=index, columns=columns
        )

        result = flatten_columns(df)

        assert list(result.columns).count("Close") == 1
        assert set(result.columns) == {"Open", "High", "Low", "Close", "Volume"}


class TestNormalizeOhlcvRobustness:
    def test_empty_frame_raises_data_unavailable(self):
        with pytest.raises(DataUnavailableError):
            normalize_ohlcv(pd.DataFrame(), ticker="WBA")

    def test_none_frame_raises_data_unavailable(self):
        with pytest.raises(DataUnavailableError):
            normalize_ohlcv(None, ticker="CTRA")

    def test_multiindex_with_duplicate_field_labels_normalizes_cleanly(self):
        """Regression: previously this shape could raise
        `ValueError: Columns must be same length as key` out of the
        `df[list(OHLCV_COLUMNS)]` selection instead of a clean
        DataUnavailableError or a usable frame."""
        df = _multiindex_frame("K")

        result = normalize_ohlcv(df, ticker="K")

        assert list(result.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert len(result) == 5

    def test_missing_required_column_raises_data_unavailable(self):
        df = pd.DataFrame(
            {"Open": [1.0], "High": [1.0], "Low": [1.0]},
            index=pd.date_range("2023-01-02", periods=1),
        )
        with pytest.raises(DataUnavailableError):
            normalize_ohlcv(df, ticker="MMC")


class TestApplySplitDividendAdjustment:
    def test_frame_without_adj_close_is_returned_unchanged(self):
        df = pd.DataFrame({"Close": [10.0, 11.0]})
        result = apply_split_dividend_adjustment(df)
        pd.testing.assert_frame_equal(result, df)

    def test_zero_close_does_not_poison_the_ratio(self):
        df = pd.DataFrame({"Close": [0.0, 10.0], "Adj Close": [0.0, 9.5]})
        result = apply_split_dividend_adjustment(df)
        assert "Adj Close" not in result.columns
        assert result["Close"].isna().sum() == 0


class TestFetchYfinanceNoStartDate:
    """Regression: `fetch_yfinance(ticker)` with no `start` is documented as
    "all available history", but yfinance's own `download()` silently
    defaults to `period="1mo"` whenever neither `start` nor `period` is
    passed - about 22 trading days, nowhere near enough for a 200-EMA
    (`MarketRegimeEngine.ema_alignment`) or any other long-lookback
    indicator. `fetch_yfinance` must pass `period="max"` explicitly in that
    case rather than falling through to yfinance's own default."""

    def test_no_start_requests_max_period(self, monkeypatch):
        captured: dict = {}

        def fake_download(ticker, start=None, end=None, period=None, **kwargs):
            captured["start"] = start
            captured["end"] = end
            captured["period"] = period
            idx = pd.date_range("2020-01-02", periods=3, freq="B")
            return pd.DataFrame(
                {
                    "Open": [1.0] * 3,
                    "High": [1.0] * 3,
                    "Low": [1.0] * 3,
                    "Close": [1.0] * 3,
                    "Volume": [100] * 3,
                },
                index=idx,
            )

        monkeypatch.setattr("yfinance.download", fake_download)

        fetch_yfinance("SPY")

        assert captured["start"] is None
        assert captured["period"] == "max"

    def test_explicit_start_does_not_request_max_period(self, monkeypatch):
        captured: dict = {}

        def fake_download(ticker, start=None, end=None, period=None, **kwargs):
            captured["start"] = start
            captured["period"] = period
            idx = pd.date_range("2020-01-02", periods=3, freq="B")
            return pd.DataFrame(
                {
                    "Open": [1.0] * 3,
                    "High": [1.0] * 3,
                    "Low": [1.0] * 3,
                    "Close": [1.0] * 3,
                    "Volume": [100] * 3,
                },
                index=idx,
            )

        monkeypatch.setattr("yfinance.download", fake_download)

        fetch_yfinance("SPY", start="2024-01-01")

        assert captured["start"] == "2024-01-01"
        assert captured["period"] is None
