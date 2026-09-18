"""
Tests for quant/data/parquet_manager.py: staleness detection, incremental
sync, and corporate-action reconciliation.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.app.quant.data.parquet_manager import (
    SYNC_STATUS_CORPORATE_ACTION_ADJUSTED,
    SYNC_STATUS_ERROR,
    SYNC_STATUS_NO_NEW_DATA,
    SYNC_STATUS_SYNCED,
    SYNC_STATUS_UNAVAILABLE,
    SYNC_STATUS_UP_TO_DATE,
    CorporateActionAdjustment,
    ParquetSyncManager,
    apply_retroactive_adjustment,
    detect_corporate_action,
    latest_market_close,
    read_max_timestamp,
)


def _raw_frame(
    dates: pd.DatetimeIndex, close: np.ndarray, adj_close: np.ndarray | None = None
) -> pd.DataFrame:
    adj_close = close if adj_close is None else adj_close
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Adj Close": adj_close,
            "Volume": 1_000_000,
        },
        index=dates,
    )


class TestLatestMarketClose:
    def test_after_close_on_a_weekday_is_today(self):
        now = pd.Timestamp("2024-01-10 17:00")  # Wednesday
        assert latest_market_close(now) == pd.Timestamp("2024-01-10")

    def test_before_close_on_a_weekday_is_the_prior_day(self):
        now = pd.Timestamp("2024-01-10 09:00")  # Wednesday, pre-market-close
        assert latest_market_close(now) == pd.Timestamp("2024-01-09")

    def test_weekend_rolls_back_to_friday(self):
        now = pd.Timestamp("2024-01-13 12:00")  # Saturday
        assert latest_market_close(now) == pd.Timestamp("2024-01-12")

    def test_monday_morning_rolls_back_to_friday(self):
        now = pd.Timestamp("2024-01-08 08:00")  # Monday, before close
        assert latest_market_close(now) == pd.Timestamp("2024-01-05")

    def test_custom_market_close_hour(self):
        now = pd.Timestamp("2024-01-10 10:00")
        assert latest_market_close(now, market_close_hour=9) == pd.Timestamp(
            "2024-01-10"
        )
        assert latest_market_close(now, market_close_hour=11) == pd.Timestamp(
            "2024-01-09"
        )


class TestReadMaxTimestamp:
    def test_missing_file_is_none(self, tmp_path):
        assert read_max_timestamp(tmp_path / "NOPE.parquet") is None

    def test_reads_max_via_metadata(self, tmp_path):
        path = tmp_path / "AAA.parquet"
        idx = pd.date_range("2024-01-01", periods=20, freq="B")
        _raw_frame(idx, np.linspace(100, 120, 20)).to_parquet(path)

        assert read_max_timestamp(path) == pd.Timestamp(idx.max())

    def test_empty_file_is_none(self, tmp_path):
        path = tmp_path / "EMPTY.parquet"
        pd.DataFrame({"Close": []}, index=pd.DatetimeIndex([])).to_parquet(path)

        assert read_max_timestamp(path) is None


class TestDetectCorporateAction:
    def test_no_action_when_factor_is_unchanged(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        close = np.linspace(100, 104, 5)
        cached = _raw_frame(idx, close)
        fresh = _raw_frame(idx[-1:], close[-1:])  # same factor (1.0)

        assert detect_corporate_action(cached, fresh) is None

    def test_detects_a_split_via_adjustment_factor_drift(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        close = np.linspace(100, 104, 5)
        cached = _raw_frame(idx, close)  # old_factor == 1.0
        anchor = idx[-1]
        fresh = _raw_frame(
            idx[-1:], close[-1:], adj_close=close[-1:] / 2
        )  # new_factor == 0.5

        action = detect_corporate_action(cached, fresh)

        assert action is not None
        assert action.anchor_date == anchor
        assert action.old_factor == pytest.approx(1.0)
        assert action.new_factor == pytest.approx(0.5)
        assert action.ratio == pytest.approx(0.5)

    def test_ignores_noise_within_tolerance(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        close = np.linspace(100, 104, 5)
        cached = _raw_frame(idx, close)
        # 0.02% drift - well under the default 0.1% tolerance.
        fresh = _raw_frame(idx[-1:], close[-1:], adj_close=close[-1:] * 1.0002)

        assert detect_corporate_action(cached, fresh) is None

    def test_none_when_anchor_missing_from_fresh(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        close = np.linspace(100, 104, 5)
        cached = _raw_frame(idx, close)
        other_day = pd.date_range("2024-02-01", periods=1, freq="B")
        fresh = _raw_frame(other_day, np.array([50.0]))

        assert detect_corporate_action(cached, fresh) is None

    def test_none_when_columns_missing(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        cached = pd.DataFrame({"Close": np.linspace(100, 104, 5)}, index=idx)
        fresh = pd.DataFrame({"Close": [50.0]}, index=idx[-1:])

        assert detect_corporate_action(cached, fresh) is None

    def test_explicit_anchor_overrides_default(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        close = np.linspace(100, 104, 5)
        cached = _raw_frame(idx, close)
        mid_anchor = idx[2]
        fresh = _raw_frame(idx[2:3], close[2:3], adj_close=close[2:3] / 4)

        action = detect_corporate_action(cached, fresh, anchor=mid_anchor)
        assert action is not None
        assert action.anchor_date == mid_anchor
        assert action.new_factor == pytest.approx(0.25)


class TestApplyRetroactiveAdjustment:
    def test_only_adjusts_adj_close(self):
        idx = pd.date_range("2024-01-01", periods=3, freq="B")
        close = np.array([100.0, 101.0, 102.0])
        df = _raw_frame(idx, close)
        adjustment = CorporateActionAdjustment(
            anchor_date=idx[-1], old_factor=1.0, new_factor=0.5
        )

        adjusted = apply_retroactive_adjustment(df, adjustment)

        pd.testing.assert_series_equal(adjusted["Close"], df["Close"])
        pd.testing.assert_series_equal(adjusted["Open"], df["Open"])
        pd.testing.assert_series_equal(adjusted["Volume"], df["Volume"])
        assert (adjusted["Adj Close"] == df["Adj Close"] * 0.5).all()

    def test_empty_frame_is_a_no_op(self):
        adjustment = CorporateActionAdjustment(
            anchor_date=pd.Timestamp("2024-01-01"), old_factor=1.0, new_factor=0.5
        )
        empty = pd.DataFrame({"Adj Close": []}, index=pd.DatetimeIndex([]))
        assert apply_retroactive_adjustment(empty, adjustment).empty

    def test_missing_adj_close_column_is_a_no_op(self):
        df = pd.DataFrame(
            {"Close": [100.0]}, index=pd.date_range("2024-01-01", periods=1)
        )
        adjustment = CorporateActionAdjustment(
            anchor_date=pd.Timestamp("2024-01-01"), old_factor=1.0, new_factor=0.5
        )
        pd.testing.assert_frame_equal(apply_retroactive_adjustment(df, adjustment), df)


@pytest.fixture
def data_dir(tmp_path) -> Path:
    return tmp_path


class TestParquetSyncManagerIsStale:
    def test_missing_cache_is_stale(self, data_dir):
        mgr = ParquetSyncManager(data_dir=data_dir)
        assert mgr.is_stale("NOPE") is True

    def test_up_to_date_cache_is_not_stale(self, data_dir):
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        _raw_frame(idx, np.linspace(100, 104, 5)).to_parquet(data_dir / "AAA.parquet")
        now = pd.Timestamp("2024-01-05 17:00")  # same day as last bar, after close

        mgr = ParquetSyncManager(data_dir=data_dir)
        assert mgr.is_stale("AAA", now=now) is False

    def test_old_cache_is_stale(self, data_dir):
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        _raw_frame(idx, np.linspace(100, 104, 5)).to_parquet(data_dir / "AAA.parquet")
        now = pd.Timestamp("2024-02-01 17:00")

        mgr = ParquetSyncManager(data_dir=data_dir)
        assert mgr.is_stale("AAA", now=now) is True


class TestParquetSyncManagerSyncTicker:
    def test_up_to_date_skips_fetch(self, data_dir):
        idx = pd.date_range("2024-01-01", periods=5, freq="B")
        _raw_frame(idx, np.linspace(100, 104, 5)).to_parquet(data_dir / "AAA.parquet")
        now = pd.Timestamp("2024-01-05 17:00")

        def fetch_fn(ticker, start, end):
            raise AssertionError("should not be called when up to date")

        mgr = ParquetSyncManager(data_dir=data_dir, fetch_fn=fetch_fn)
        result = mgr.sync_ticker("AAA", now=now)

        assert result.status == SYNC_STATUS_UP_TO_DATE

    def test_first_sync_with_no_existing_cache(self, data_dir):
        new_idx = pd.date_range("2024-01-01", periods=5, freq="B")
        fresh = _raw_frame(new_idx, np.linspace(100, 104, 5))

        mgr = ParquetSyncManager(data_dir=data_dir, fetch_fn=lambda t, s, e: fresh)
        result = mgr.sync_ticker("NEW", now=pd.Timestamp("2024-01-10 17:00"))

        assert result.status == SYNC_STATUS_SYNCED
        assert result.rows_added == 5
        assert (data_dir / "NEW.parquet").exists()

    def test_appends_new_rows_without_corporate_action(self, data_dir):
        old_idx = pd.date_range("2024-01-01", periods=5, freq="B")
        old_close = np.linspace(100, 104, 5)
        _raw_frame(old_idx, old_close).to_parquet(data_dir / "AAA.parquet")

        anchor = old_idx[-1]
        new_dates = pd.date_range(anchor + pd.Timedelta(days=1), periods=3, freq="B")
        fresh_idx = old_idx[-1:].append(new_dates)
        fresh_close = np.concatenate([old_close[-1:], [105.0, 106.0, 107.0]])
        fresh = _raw_frame(fresh_idx, fresh_close)

        mgr = ParquetSyncManager(data_dir=data_dir, fetch_fn=lambda t, s, e: fresh)
        result = mgr.sync_ticker("AAA", now=pd.Timestamp("2024-01-15 17:00"))

        assert result.status == SYNC_STATUS_SYNCED
        assert result.rows_added == 3
        assert result.corporate_action is None

        out = pd.read_parquet(data_dir / "AAA.parquet")
        assert len(out) == 8
        # Pre-anchor history is untouched.
        assert out["Close"].iloc[0] == pytest.approx(old_close[0])

    def test_detects_and_reconciles_a_split(self, data_dir):
        old_idx = pd.date_range("2024-01-01", periods=5, freq="B")
        old_close = np.linspace(100, 104, 5)
        _raw_frame(old_idx, old_close).to_parquet(data_dir / "AAA.parquet")

        anchor = old_idx[-1]
        new_dates = pd.date_range(anchor + pd.Timedelta(days=1), periods=2, freq="B")
        fresh_idx = old_idx[-1:].append(new_dates)
        # Raw close at the anchor stays the true historical print; Adj Close
        # there (and for every new bar) reflects the post-split basis.
        fresh_close = np.array([old_close[-1], 55.0, 55.5])
        fresh_adj = np.array([old_close[-1] / 2, 55.0, 55.5])
        fresh = _raw_frame(fresh_idx, fresh_close, adj_close=fresh_adj)

        mgr = ParquetSyncManager(data_dir=data_dir, fetch_fn=lambda t, s, e: fresh)
        result = mgr.sync_ticker("AAA", now=pd.Timestamp("2024-01-15 17:00"))

        assert result.status == SYNC_STATUS_CORPORATE_ACTION_ADJUSTED
        assert result.corporate_action is not None
        assert result.corporate_action.ratio == pytest.approx(0.5)

        out = pd.read_parquet(data_dir / "AAA.parquet")
        # Raw historical Close is untouched...
        assert out["Close"].iloc[0] == pytest.approx(old_close[0])
        # ...but Adj Close for that same old bar is corrected onto the new basis.
        assert out["Adj Close"].iloc[0] == pytest.approx(old_close[0] / 2)

    def test_no_new_data_when_fetch_returns_empty_but_cache_exists(self, data_dir):
        old_idx = pd.date_range("2024-01-01", periods=5, freq="B")
        _raw_frame(old_idx, np.linspace(100, 104, 5)).to_parquet(
            data_dir / "AAA.parquet"
        )

        mgr = ParquetSyncManager(
            data_dir=data_dir, fetch_fn=lambda t, s, e: pd.DataFrame()
        )
        result = mgr.sync_ticker("AAA", now=pd.Timestamp("2024-02-01 17:00"))

        assert result.status == SYNC_STATUS_NO_NEW_DATA

    def test_unavailable_when_fetch_returns_empty_and_no_cache(self, data_dir):
        mgr = ParquetSyncManager(
            data_dir=data_dir, fetch_fn=lambda t, s, e: pd.DataFrame()
        )
        result = mgr.sync_ticker("NOPE", now=pd.Timestamp("2024-02-01 17:00"))

        assert result.status == SYNC_STATUS_UNAVAILABLE

    def test_fetch_error_is_isolated_as_a_result_not_raised(self, data_dir):
        def failing_fetch(ticker, start, end):
            raise RuntimeError("network exploded")

        mgr = ParquetSyncManager(data_dir=data_dir, fetch_fn=failing_fetch)
        result = mgr.sync_ticker("AAA", now=pd.Timestamp("2024-02-01 17:00"))

        assert result.status == SYNC_STATUS_ERROR
        assert "network exploded" in result.error

    def test_as_dict_is_json_ready(self, data_dir):
        fresh = _raw_frame(
            pd.date_range("2024-01-01", periods=3, freq="B"), np.array([1.0, 2.0, 3.0])
        )
        mgr = ParquetSyncManager(data_dir=data_dir, fetch_fn=lambda t, s, e: fresh)
        result = mgr.sync_ticker("AAA", now=pd.Timestamp("2024-01-10 17:00"))

        payload = result.as_dict()
        assert payload["ticker"] == "AAA"
        assert payload["status"] == SYNC_STATUS_SYNCED
        assert payload["corporate_action"] is None


class TestParquetSyncManagerSyncUniverse:
    def test_syncs_every_ticker_and_isolates_failures(self, data_dir):
        fresh = _raw_frame(
            pd.date_range("2024-01-01", periods=3, freq="B"), np.array([1.0, 2.0, 3.0])
        )

        def fetch_fn(ticker, start, end):
            if ticker == "BAD":
                raise RuntimeError("boom")
            return fresh

        mgr = ParquetSyncManager(data_dir=data_dir, fetch_fn=fetch_fn)
        results = mgr.sync_universe(
            ["AAA", "BAD", "CCC"], now=pd.Timestamp("2024-01-10 17:00")
        )

        statuses = {r.ticker: r.status for r in results}
        assert statuses["AAA"] == SYNC_STATUS_SYNCED
        assert statuses["BAD"] == SYNC_STATUS_ERROR
        assert statuses["CCC"] == SYNC_STATUS_SYNCED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
