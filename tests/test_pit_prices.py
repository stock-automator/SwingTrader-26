"""Daily bars for point-in-time S&P 500 members missing from the cache (04b)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from backend.app.data import pit_prices as pp
from backend.app.data.loader import load_prices


def _membership(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "ticker"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def _bars(start: str, n: int, price: float = 10.0) -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n)
    close = np.full(n, price)
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6},
        index=idx,
    )


def test_needed_lists_uncached_members_with_asof_a_month_before_they_left(tmp_path):
    """asof resolves the symbol the member had shortly before it left: a
    symbol renamed after 2015 (TSO -> ANDV) does not exist at the first
    snapshot, and on the removal day it can already belong to a successor."""
    (tmp_path / "AAPL.parquet").write_bytes(b"")
    m = _membership(
        [
            ("2016-01-31", "AAPL"),
            ("2016-01-31", "AA"),
            ("2016-06-30", "AA"),
            ("2016-10-31", "AA"),
            ("2016-02-29", "TWTR"),
        ]
    )
    assert pp.needed(m, [tmp_path]) == {"AA": "2016-09-26", "TWTR": "2016-02-29"}


def test_fetch_all_applies_query_overrides_and_cuts_at_their_end(tmp_path):
    seen = []

    def alpaca(symbol, asof):
        seen.append((symbol, asof))
        return _bars("2019-03-01", 30)

    overrides = {"TFCFA": ("FOXA", "2019-01-24", "2019-03-19")}
    pp.fetch_all(
        {"TFCFA": "2019-01-27"},
        tmp_path,
        sources=[("alpaca", alpaca)],
        overrides=overrides,
    )
    assert seen == [("FOXA", "2019-01-24")]
    df = load_prices("TFCFA", data_dir=tmp_path, allow_download=False)
    assert df.index[-1] == pd.Timestamp("2019-03-19")


def test_fetch_all_overwrite_refetches_existing_files(tmp_path):
    pp.write_bars(_bars("2016-01-04", 5), tmp_path / "A1.parquet")
    status = pp.fetch_all(
        {"A1": "2016-01-31"},
        tmp_path,
        sources=[("alpaca", lambda t, a: _bars("2016-01-04", 9))],
        overwrite=True,
    )
    assert status["A1"]["source"] == "alpaca"
    assert len(load_prices("A1", data_dir=tmp_path, allow_download=False)) == 9


def test_fetch_all_tries_alpaca_then_yfinance_and_writes_loader_format(tmp_path):
    calls = []

    def alpaca(ticker, asof):
        calls.append(("alpaca", ticker, asof))
        return _bars("2016-01-04", 30) if ticker == "A1" else pd.DataFrame()

    def yf(ticker, asof):
        calls.append(("yfinance", ticker, asof))
        return _bars("2016-01-04", 20) if ticker == "Y1" else pd.DataFrame()

    status = pp.fetch_all(
        {"A1": "2016-01-31", "Y1": "2016-01-31", "NONE": "2016-01-31"},
        tmp_path,
        sources=[("alpaca", alpaca), ("yfinance", yf)],
    )
    assert status["A1"]["source"] == "alpaca"
    assert status["Y1"]["source"] == "yfinance"
    assert status["NONE"]["source"] is None
    assert ("yfinance", "A1", "2016-01-31") not in calls
    df = load_prices("A1", data_dir=tmp_path, allow_download=False)
    assert len(df) == 30 and list(df.columns)[:5] == [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]
    assert not (tmp_path / "NONE.parquet").exists()
    saved = json.loads((tmp_path / pp.STATUS_FILE).read_text())
    assert saved["NONE"]["source"] is None


def test_fetch_all_skips_cached_and_negative_cached_tickers(tmp_path):
    pp.write_bars(_bars("2016-01-04", 5), tmp_path / "A1.parquet")
    (tmp_path / pp.STATUS_FILE).write_text(
        json.dumps({"GONE": {"source": None, "errors": {}}})
    )

    def never(ticker, asof):
        raise AssertionError(f"fetched {ticker}")

    status = pp.fetch_all(
        {"A1": "2016-01-31", "GONE": "2016-01-31"},
        tmp_path,
        sources=[("alpaca", never)],
    )
    assert status["A1"]["source"] == "cached"
    assert status["GONE"]["source"] is None
    status = pp.fetch_all(
        {"GONE": "2016-01-31"},
        tmp_path,
        sources=[("alpaca", lambda t, a: _bars("2016-01-04", 5))],
        retry_failed=True,
    )
    assert status["GONE"]["source"] == "alpaca"


def test_fetch_all_records_source_errors_loudly(tmp_path, caplog):
    def broken(ticker, asof):
        raise RuntimeError("HTTP 500 for key=SECRET123")

    status = pp.fetch_all(
        {"X": "2016-01-31"},
        tmp_path,
        sources=[("alpaca", broken)],
        secrets=["SECRET123"],
    )
    err = status["X"]["errors"]["alpaca"]
    assert "HTTP 500" in err and "SECRET123" not in err
    assert "SECRET123" not in caplog.text
    assert any(r.levelname == "WARNING" and "X" in r.message for r in caplog.records)


def test_alpaca_frame_maps_columns_and_drops_tz():
    raw = pd.DataFrame(
        {
            "symbol": ["AA", "AA"],
            "timestamp": pd.to_datetime(
                ["2016-01-04 05:00", "2016-01-05 05:00"], utc=True
            ),
            "open": [1.0, 2],
            "high": [1.0, 2],
            "low": [1.0, 2],
            "close": [1.0, 2],
            "volume": [10.0, 20],
            "trade_count": [1, 1],
            "vwap": [1.0, 2],
        }
    ).set_index(["symbol", "timestamp"])
    out = pp.from_alpaca(raw)
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert out.index.tolist() == pd.to_datetime(["2016-01-04", "2016-01-05"]).tolist()
    assert out.index.tz is None


@pytest.mark.parametrize("empty", [pd.DataFrame(), None])
def test_alpaca_frame_handles_empty(empty):
    assert pp.from_alpaca(empty).empty


def test_fetch_all_tries_the_preferred_source_first_for_listed_tickers(tmp_path):
    calls = []

    def source(name):
        def fetch(symbol, asof):
            calls.append((name, symbol))
            return _bars("2015-01-02", 5)

        return fetch

    status = pp.fetch_all(
        {"BBWI": "2024-08-26", "AAL": "2024-07-27"},
        tmp_path,
        sources=[("alpaca", source("alpaca")), ("yfinance", source("yfinance"))],
        prefer={"BBWI": "yfinance"},
    )
    assert status["BBWI"]["source"] == "yfinance"
    assert status["AAL"]["source"] == "alpaca"
    assert calls == [("yfinance", "BBWI"), ("alpaca", "AAL")]
