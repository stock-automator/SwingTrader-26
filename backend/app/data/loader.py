"""
Historical bar access.

Resolution order: the on-disk parquet cache written by `data/agent.py`
first, then a live yfinance fetch for anything not cached - notably SPY,
which the benchmark engine needs on every run and which the cache does not
carry.

Both sources are normalised onto one contract before returning: flat
`Open/High/Low/Close/Volume` columns, a sorted unique `DatetimeIndex`, and
prices on a *total-return* basis. That last point is the subtle one - see
`apply_split_dividend_adjustment`.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

#: Where `data/agent.py` writes its parquet cache.
DATA_DIR = Path("data/raw")

#: The contract every frame leaving this module satisfies.
OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")

#: Benchmark every strategy is measured against.
SPY_TICKER = "SPY"


class DataUnavailableError(RuntimeError):
    """No usable bars could be resolved for a ticker.

    Distinct from `ValueError`: the request was well-formed, the data just
    is not there (uncached ticker, delisted symbol, provider outage). The
    API layer maps this to 503/404 rather than 422.
    """


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop yfinance's ticker column level, e.g. `('Close', 'NVDA')` -> `'Close'`.

    yfinance returns `(field, ticker)` MultiIndex columns, and the parquet
    cache was written straight from it, so cached frames carry the same
    shape. Single-ticker frames are the only case this module handles, so
    the ticker level is redundant.
    """
    if not isinstance(df.columns, pd.MultiIndex):
        return df

    df = df.copy()
    df.columns = df.columns.droplevel(-1)
    # A delisted/renamed ticker can make yfinance return a frame where the
    # dropped level leaves duplicate labels (e.g. a provider-side ticker
    # alias). Downstream `df[list(OHLCV_COLUMNS)]` selection against
    # duplicate labels returns a wider-than-expected frame and can raise
    # "Columns must be same length as key" several calls later - deduped
    # here, at the one place that knows why duplicates could exist, rather
    # than guarded defensively at every call site.
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    return df


def apply_split_dividend_adjustment(df: pd.DataFrame) -> pd.DataFrame:
    """Put OHLC on a total-return basis using `Adj Close`, then drop it.

    The cache was written with yfinance's older `auto_adjust=False` default,
    so it carries a raw `Close` plus a separate split/dividend-adjusted
    `Adj Close`. Live fetches use `auto_adjust=True` and are already
    adjusted. Comparing an unadjusted cached asset against an adjusted SPY
    understates the asset by its entire dividend stream - which is exactly
    the number this platform reports - so the two sources are reconciled
    here rather than at the call site.

    Scales OHLC by `Adj Close / Close` and inflates Volume by its
    reciprocal, the standard back-adjustment. A frame without `Adj Close`
    is assumed already adjusted and returned unchanged.
    """
    if "Adj Close" not in df.columns or "Close" not in df.columns:
        return df

    df = df.copy()
    # Guard against a zero/NaN close in the cache: the ratio would be inf or
    # NaN and would silently poison every downstream price. Those bars keep
    # their unadjusted values instead.
    ratio = (df["Adj Close"] / df["Close"]).replace(
        [float("inf"), float("-inf")], pd.NA
    )
    ratio = ratio.astype("float64").fillna(1.0)

    for column in ("Open", "High", "Low", "Close"):
        if column in df.columns:
            df[column] = df[column] * ratio
    if "Volume" in df.columns:
        df["Volume"] = df["Volume"] / ratio

    return df.drop(columns=["Adj Close"])


def normalize_ohlcv(df: pd.DataFrame, ticker: str = "") -> pd.DataFrame:
    """Coerce a raw provider/cache frame onto the OHLCV contract.

    Raises:
        DataUnavailableError: if the frame is empty, is missing a required
            column, or has no rows left once non-finite prices are dropped.
    """
    label = f" for {ticker}" if ticker else ""

    if df is None or df.empty:
        raise DataUnavailableError(f"No bars returned{label}")

    df = apply_split_dividend_adjustment(flatten_columns(df))

    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise DataUnavailableError(f"Bars{label} are missing column(s): {missing}")

    df = df[list(OHLCV_COLUMNS)].astype("float64")
    df.index = pd.to_datetime(df.index).tz_localize(None)
    # Duplicate timestamps break `backtesting`'s bar indexing and would make
    # two equity curves of the same window different lengths.
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    if df.empty:
        raise DataUnavailableError(f"No usable bars{label} after cleaning")

    return df


def load_cached(ticker: str, data_dir: Path | str = DATA_DIR) -> pd.DataFrame | None:
    """Read `{data_dir}/{ticker}.parquet`, or `None` if it isn't cached."""
    path = Path(data_dir) / f"{ticker.upper()}.parquet"
    if not path.exists():
        return None

    return normalize_ohlcv(pd.read_parquet(path), ticker)


def fetch_yfinance(
    ticker: str, start: str | None = None, end: str | None = None
) -> pd.DataFrame:
    """Download adjusted daily bars for one ticker.

    `end` is passed through to yfinance, whose `end` is *exclusive*; it is
    advanced by a day so the caller's inclusive end date is included.

    Raises:
        DataUnavailableError: on any provider failure or empty response.
            yfinance signals a bad ticker by returning an empty frame rather
            than raising, so both paths land here.
    """
    import yfinance as yf

    if end is not None:
        end = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    # yfinance's own `download` defaults to `period="1mo"` whenever neither
    # `start` nor `period` is passed - so `start=None` here (this function's
    # documented "all available history") would otherwise silently come
    # back with about 22 trading days. Passing `period="max"` explicitly
    # only when there's no `start` restores that documented contract; a
    # caller-supplied `start` still takes precedence exactly as before
    # (`start`/`period` are mutually exclusive to yfinance).
    period = "max" if start is None else None

    try:
        raw = yf.download(
            ticker,
            start=start,
            end=end,
            period=period,
            progress=False,
            auto_adjust=True,
            actions=False,
        )
    except Exception as exc:  # network, rate limit, schema change
        raise DataUnavailableError(
            f"yfinance fetch failed for {ticker}: {exc}"
        ) from exc

    return normalize_ohlcv(raw, ticker)


def fetch_earnings_dates(ticker: str, limit: int = 8) -> list[pd.Timestamp]:
    """Upcoming/recent earnings report dates for `ticker`, via yfinance.

    Used by the catalyst filter (`quant/screener.py`'s `CatalystFilter`) to
    suppress new long setups going into an earnings print. Returns dates
    sorted ascending, deduplicated, and timezone-naive - the same convention
    `normalize_ohlcv` puts price bars on, so a setup's `as_of` date and an
    earnings date are directly comparable.

    Args:
        ticker: Symbol, case-insensitive.
        limit: Max rows to request from the provider's earnings calendar.

    Returns:
        Empty list if the provider has no earnings calendar for this
        ticker (e.g. an ETF, or a symbol yfinance doesn't cover) - that is a
        "nothing to filter on" result, not an error.

    Raises:
        DataUnavailableError: on a provider failure (network, rate limit,
            schema change). A missing calendar is not this - see above.
    """
    import yfinance as yf

    try:
        raw = yf.Ticker(ticker.upper()).get_earnings_dates(limit=limit)
    except Exception as exc:  # network, rate limit, schema change
        raise DataUnavailableError(
            f"earnings calendar fetch failed for {ticker}: {exc}"
        ) from exc

    if raw is None or raw.empty:
        return []

    dates = pd.DatetimeIndex(raw.index).tz_localize(None)
    return sorted(pd.Timestamp(d).normalize() for d in dates.unique())


def fetch_stock_splits(ticker: str) -> list[pd.Timestamp]:
    """Historical stock-split ex-dates for `ticker`, via yfinance.

    Used by `execution.guards.EarningsLockoutGuard` to suppress new swing
    entries around a split, the same corporate-action-risk reasoning as the
    earnings blackout above (a split doesn't change fundamentals, but it can
    move the tape in ways this project's strategies weren't fit to price
    in). Only past/announced splits yfinance already has ex-dates for are
    returned - there is no forward-looking "next split" calendar the way
    `fetch_earnings_dates` has one for earnings.

    Returns:
        Empty list if the provider has no split history for this ticker -
        a "nothing to filter on" result, not an error.

    Raises:
        DataUnavailableError: on a provider failure (network, rate limit,
            schema change). A missing/empty split history is not this.
    """
    import yfinance as yf

    try:
        raw = yf.Ticker(ticker.upper()).splits
    except Exception as exc:  # network, rate limit, schema change
        raise DataUnavailableError(
            f"split history fetch failed for {ticker}: {exc}"
        ) from exc

    if raw is None or raw.empty:
        return []

    dates = pd.DatetimeIndex(raw.index).tz_localize(None)
    return sorted(pd.Timestamp(d).normalize() for d in dates.unique())


def load_prices(
    ticker: str,
    start: str | None = None,
    end: str | None = None,
    data_dir: Path | str = DATA_DIR,
    allow_download: bool = True,
) -> pd.DataFrame:
    """Resolve daily bars for `ticker`, cache first then yfinance.

    Args:
        ticker: Symbol, case-insensitive.
        start: Inclusive ISO start date, or None for all available history.
        end: Inclusive ISO end date, or None for through the last bar.
        data_dir: Parquet cache directory.
        allow_download: If False, never hit the network - raise if the
            ticker is not cached. Tests set this so a suite cannot silently
            depend on a live provider.

    Raises:
        DataUnavailableError: if neither source yields bars in the window.
    """
    ticker = ticker.upper()
    df = load_cached(ticker, data_dir)

    if df is not None:
        window = _slice_window(df, start, end)
        # A cached ticker whose window is empty is usually a request that
        # starts after the cache's last refresh, so fall through to the live
        # provider rather than reporting "no data" for a symbol we have.
        if not window.empty:
            return window
    elif not allow_download:
        raise DataUnavailableError(
            f"{ticker} is not in the parquet cache and downloads are disabled"
        )

    if not allow_download:
        raise DataUnavailableError(
            f"Cached history for {ticker} does not cover "
            f"{start or 'start'}..{end or 'end'} and downloads are disabled"
        )

    return _slice_window(fetch_yfinance(ticker, start, end), start, end)


def _slice_window(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    """Inclusive date slice. `.loc` on a sorted DatetimeIndex includes both
    endpoints, unlike yfinance's exclusive `end`."""
    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end)]
    return df


def cached_tickers(data_dir: Path | str = DATA_DIR) -> list[str]:
    """Sorted symbols available in the parquet cache."""
    directory = Path(data_dir)
    if not directory.exists():
        return []

    return sorted(p.stem.upper() for p in directory.glob("*.parquet"))
