"""
Parquet cache staleness detection, incremental sync, and corporate-action
reconciliation.

The on-disk cache (`data/raw/{TICKER}.parquet`) is a raw yfinance dump: OHLC,
`Adj Close`, and `Volume`. `Adj Close / Close` is yfinance's *cumulative*
split/dividend adjustment factor as of the moment a file was written. A
stock that splits or pays a dividend *after* the last sync retroactively
changes that factor for every historical bar - so re-fetching the single
overlapping "anchor" bar (the cache's own last row) and comparing its
adjustment factor then vs. now is enough to detect the event without
re-downloading or re-diffing the whole history. This is the same
`Adj Close`-vs-`Close` ratio `data.loader.apply_split_dividend_adjustment`
already uses to put a frame on a total-return basis; `apply_retroactive_
adjustment` below applies that same scaling backwards, to the stale rows.

`read_max_timestamp` never loads OHLCV data to answer "how fresh is this
file": it reads the parquet footer's per-row-group min/max statistics for
just the index column, falling back to a column-pruned read (which physically
touches only the index column, not the price/volume columns) if a file
lacks those statistics.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd
import pyarrow.parquet as pq

from backend.app.data.loader import flatten_columns

#: First bar requested for a ticker with no existing cache file.
DEFAULT_LOOKBACK_START = "2015-01-01"

#: Local hour (24h) after which the current trading day is considered
#: closed, for `latest_market_close`'s "has today's bar landed yet" check.
DEFAULT_MARKET_CLOSE_HOUR = 16

#: Relative change in the Adj-Close/Close factor, at the shared anchor bar,
#: above which a corporate action (not floating-point/provider noise) is
#: assumed to have occurred.
DEFAULT_CORPORATE_ACTION_TOLERANCE = 0.001

SYNC_STATUS_UP_TO_DATE = "up_to_date"
SYNC_STATUS_SYNCED = "synced"
SYNC_STATUS_CORPORATE_ACTION_ADJUSTED = "corporate_action_adjusted"
SYNC_STATUS_NO_NEW_DATA = "no_new_data"
SYNC_STATUS_UNAVAILABLE = "unavailable"
SYNC_STATUS_ERROR = "error"


def latest_market_close(
    now: pd.Timestamp | None = None,
    market_close_hour: int = DEFAULT_MARKET_CLOSE_HOUR,
) -> pd.Timestamp:
    """Most recent trading-day close date at/before `now`.

    Mon-Fri calendar, no market-holiday feed - the same tradeoff
    `quant.screener.CatalystFilter` makes, and enough precision for a
    staleness check (a holiday just makes the cache look one day "staler"
    than it truly is, which self-corrects on the next real trading day).

    Args:
        now: Reference instant. Defaults to the current local time.
        market_close_hour: Local hour (24h) the market is considered closed
            at. Before this hour on a trading day, that day hasn't
            produced a closing bar yet, so the answer rolls back one day.
    """
    now = pd.Timestamp.now() if now is None else pd.Timestamp(now)
    candidate = now.normalize()
    if now.hour < market_close_hour:
        candidate -= pd.Timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= pd.Timedelta(days=1)
    return candidate


def _pandas_index_column_name(metadata: pq.FileMetaData) -> str | None:
    """The single index column pandas recorded when it wrote this file, or
    `None` if that can't be determined (no pandas metadata, or a
    multi-level index this module doesn't need to support)."""
    schema = metadata.schema.to_arrow_schema()
    raw = schema.metadata.get(b"pandas") if schema.metadata else None
    if not raw:
        return None

    try:
        pandas_meta = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    index_columns = pandas_meta.get("index_columns", [])
    if len(index_columns) != 1 or not isinstance(index_columns[0], str):
        return None
    return index_columns[0]


def _read_max_timestamp_fallback(path: Path) -> pd.Timestamp | None:
    """Column-pruned read: reconstructs just the index, touching none of
    the OHLCV column data - used when row-group statistics aren't
    available to answer the metadata-only question directly."""
    df = pd.read_parquet(path, columns=[])
    if df.empty:
        return None
    return pd.Timestamp(df.index.max())


def read_max_timestamp(path: Path) -> pd.Timestamp | None:
    """The latest bar date in `path`, read from the parquet footer's
    row-group statistics wherever possible - never the full DataFrame.

    Returns:
        `None` if `path` doesn't exist or is empty.
    """
    if not path.exists():
        return None

    try:
        metadata = pq.read_metadata(path)
    except Exception:
        return _read_max_timestamp_fallback(path)

    index_column = _pandas_index_column_name(metadata)
    if index_column is None:
        return _read_max_timestamp_fallback(path)

    schema = metadata.schema.to_arrow_schema()
    col_idx = schema.get_field_index(index_column)
    if col_idx < 0:
        return _read_max_timestamp_fallback(path)

    max_value = None
    for row_group in range(metadata.num_row_groups):
        stats = metadata.row_group(row_group).column(col_idx).statistics
        if stats is None or not stats.has_min_max:
            return _read_max_timestamp_fallback(path)
        if max_value is None or stats.max > max_value:
            max_value = stats.max

    if max_value is None:
        return _read_max_timestamp_fallback(path)

    timestamp = pd.Timestamp(max_value)
    return timestamp.tz_localize(None) if timestamp.tzinfo is not None else timestamp


@dataclass(frozen=True)
class CorporateActionAdjustment:
    """A detected split/dividend event: the cumulative adjustment factor at
    `anchor_date` changed from `old_factor` to `new_factor` between two
    syncs."""

    anchor_date: pd.Timestamp
    old_factor: float
    new_factor: float

    @property
    def ratio(self) -> float:
        """Multiply stale OHLC by this (and divide Volume by it) to bring
        pre-event history onto the new adjusted basis."""
        return self.new_factor / self.old_factor

    def as_dict(self) -> dict:
        return {
            "anchor_date": self.anchor_date.strftime("%Y-%m-%d"),
            "old_factor": round(self.old_factor, 6),
            "new_factor": round(self.new_factor, 6),
            "ratio": round(self.ratio, 6),
        }


def _adjustment_factor(df: pd.DataFrame, as_of: pd.Timestamp) -> float | None:
    """`Adj Close / Close` on `as_of`, or `None` if that can't be computed
    (missing bar/columns, or a non-finite/non-positive price)."""
    if (
        as_of not in df.index
        or "Close" not in df.columns
        or "Adj Close" not in df.columns
    ):
        return None

    row = df.loc[as_of]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]

    close = float(row["Close"])
    adj_close = float(row["Adj Close"])
    if (
        not math.isfinite(close)
        or close <= 0
        or not math.isfinite(adj_close)
        or adj_close <= 0
    ):
        return None
    return adj_close / close


def detect_corporate_action(
    cached_df: pd.DataFrame,
    fresh_df: pd.DataFrame,
    anchor: pd.Timestamp | None = None,
    tolerance: float = DEFAULT_CORPORATE_ACTION_TOLERANCE,
) -> CorporateActionAdjustment | None:
    """Whether a split/dividend occurred between two syncs, detected by
    comparing the cumulative adjustment factor at a shared historical bar.

    Args:
        cached_df: The stale on-disk frame (must carry `Close`/`Adj Close`).
        fresh_df: A freshly re-fetched frame covering (at least) `anchor`.
        anchor: The shared bar to compare. Defaults to `cached_df`'s last
            row - the cache's own most recent bar, which is the one most
            likely to still be present in a small incremental re-fetch.
        tolerance: Relative change in the adjustment factor below which a
            difference is treated as noise, not a real event.

    Returns:
        `None` if either frame lacks the anchor bar or the required
        columns, or the factor didn't move beyond `tolerance`.
    """
    if anchor is None:
        if cached_df.empty:
            return None
        anchor = cached_df.index.max()

    old_factor = _adjustment_factor(cached_df, anchor)
    new_factor = _adjustment_factor(fresh_df, anchor)
    if old_factor is None or new_factor is None:
        return None

    relative_change = abs(new_factor - old_factor) / old_factor
    if relative_change <= tolerance:
        return None

    return CorporateActionAdjustment(
        anchor_date=pd.Timestamp(anchor), old_factor=old_factor, new_factor=new_factor
    )


def apply_retroactive_adjustment(
    df: pd.DataFrame, adjustment: CorporateActionAdjustment
) -> pd.DataFrame:
    """Correct `df` (pre-event history)'s `Adj Close` onto the basis a
    newly detected corporate action implies.

    Deliberately touches *only* `Adj Close`. Raw `Open`/`High`/`Low`/
    `Close`/`Volume` are the immutable historical trade prints - a split
    doesn't change what actually traded on an old date, only how that old
    close maps onto today's share count. Rescaling the raw columns too
    would double-apply the adjustment the next time `data.loader.
    apply_split_dividend_adjustment` derives its own `Adj Close / Close`
    ratio from this file and scales OHLC by it - or worse, silently cancel
    the correction back out if `old_factor` happened to equal 1.0 (a file
    that had never seen a split before this one), since scaling both
    columns by the same ratio leaves their ratio to each other unchanged.
    """
    if df.empty or "Adj Close" not in df.columns:
        return df.copy()

    df = df.copy()
    df["Adj Close"] = df["Adj Close"] * adjustment.ratio
    return df


@dataclass(frozen=True)
class SyncResult:
    """Outcome of syncing one ticker."""

    ticker: str
    status: str
    rows_added: int = 0
    corporate_action: CorporateActionAdjustment | None = None
    error: str | None = None
    last_fetched_at: pd.Timestamp | None = None

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "status": self.status,
            "rows_added": self.rows_added,
            "corporate_action": (
                self.corporate_action.as_dict() if self.corporate_action else None
            ),
            "error": self.error,
            "last_fetched_at": (
                self.last_fetched_at.strftime("%Y-%m-%d")
                if self.last_fetched_at is not None
                else None
            ),
        }


def _read_cached_raw(path: Path) -> pd.DataFrame | None:
    """The on-disk frame as-is (columns flattened, index normalised) -
    `Adj Close` intact, unlike `data.loader.load_cached`'s adjusted-and-
    dropped public contract. `None` if there is no cache file yet."""
    if not path.exists():
        return None

    df = flatten_columns(pd.read_parquet(path))
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df[~df.index.duplicated(keep="last")].sort_index()


def _write_parquet(path: Path, df: pd.DataFrame) -> None:
    """Atomic write: a partial write from a crash mid-sync can never leave
    a corrupt file in place of a good one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    df.to_parquet(tmp_path, engine="pyarrow")
    tmp_path.replace(path)


def _default_fetch_raw(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Raw OHLCV + `Adj Close` via yfinance (`auto_adjust=False`) - the
    format the cache and corporate-action detection are built on.

    `end` is advanced by a day, matching `data.loader.fetch_yfinance`'s own
    handling of yfinance's exclusive `end` bound.
    """
    import yfinance as yf

    end_exclusive = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    raw = yf.download(
        ticker,
        start=start,
        end=end_exclusive,
        auto_adjust=False,
        progress=False,
        actions=False,
    )
    if raw is None or raw.empty:
        return pd.DataFrame()

    df = flatten_columns(raw)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


class ParquetSyncManager:
    """Keeps a directory of per-ticker parquet caches current.

    Args:
        data_dir: Directory holding `{TICKER}.parquet` files.
        market_close_hour: See `latest_market_close`.
        fetch_fn: `(ticker, start, end) -> raw OHLCV+Adj Close DataFrame`.
            Defaults to a yfinance-backed fetch; tests inject a fake to
            stay off the network.
        lookback_start: First bar requested for a ticker with no existing
            cache file.
        corporate_action_tolerance: See `detect_corporate_action`.
    """

    def __init__(
        self,
        data_dir: Path | str = Path("data/raw"),
        market_close_hour: int = DEFAULT_MARKET_CLOSE_HOUR,
        fetch_fn: Callable[[str, str, str], pd.DataFrame] | None = None,
        lookback_start: str = DEFAULT_LOOKBACK_START,
        corporate_action_tolerance: float = DEFAULT_CORPORATE_ACTION_TOLERANCE,
    ):
        self.data_dir = Path(data_dir)
        self.market_close_hour = market_close_hour
        self.fetch_fn = fetch_fn or _default_fetch_raw
        self.lookback_start = lookback_start
        self.corporate_action_tolerance = corporate_action_tolerance

    def path_for(self, ticker: str) -> Path:
        return self.data_dir / f"{ticker.upper()}.parquet"

    def get_max_timestamp(self, ticker: str) -> pd.Timestamp | None:
        return read_max_timestamp(self.path_for(ticker))

    def is_stale(self, ticker: str, now: pd.Timestamp | None = None) -> bool:
        """Whether `ticker`'s cache's last bar is older than the latest
        completed trading session. A ticker with no cache file at all
        counts as stale."""
        max_timestamp = self.get_max_timestamp(ticker)
        if max_timestamp is None:
            return True
        return max_timestamp.normalize() < latest_market_close(
            now, self.market_close_hour
        )

    def sync_ticker(self, ticker: str, now: pd.Timestamp | None = None) -> SyncResult:
        """Bring one ticker's cache up to date, reconciling any corporate
        action detected along the way. Never raises - a per-ticker failure
        comes back as `SyncResult(status="error")` so a batch sync
        (`sync_universe`) can't be taken down by one bad symbol."""
        ticker = ticker.upper()
        try:
            if not self.is_stale(ticker, now):
                existing = read_max_timestamp(self.path_for(ticker))
                return SyncResult(
                    ticker=ticker,
                    status=SYNC_STATUS_UP_TO_DATE,
                    last_fetched_at=existing,
                )

            path = self.path_for(ticker)
            cached_df = _read_cached_raw(path)
            now_ts = pd.Timestamp.now() if now is None else pd.Timestamp(now)
            start = (
                cached_df.index.max().strftime("%Y-%m-%d")
                if cached_df is not None and not cached_df.empty
                else self.lookback_start
            )
            end = now_ts.strftime("%Y-%m-%d")

            fresh_df = self.fetch_fn(ticker, start, end)
            if fresh_df is None or fresh_df.empty:
                existing = read_max_timestamp(path) if path.exists() else None
                status = (
                    SYNC_STATUS_UNAVAILABLE
                    if cached_df is None or cached_df.empty
                    else SYNC_STATUS_NO_NEW_DATA
                )
                return SyncResult(
                    ticker=ticker,
                    status=status,
                    last_fetched_at=existing,
                )

            fresh_df = fresh_df[~fresh_df.index.duplicated(keep="last")].sort_index()

            corporate_action = None
            if cached_df is not None and not cached_df.empty:
                anchor = cached_df.index.max()
                corporate_action = detect_corporate_action(
                    cached_df, fresh_df, anchor, self.corporate_action_tolerance
                )
                if anchor in fresh_df.index:
                    carried = cached_df.loc[cached_df.index < anchor]
                    if corporate_action is not None:
                        carried = apply_retroactive_adjustment(
                            carried, corporate_action
                        )
                else:
                    # The anchor bar didn't come back in the re-fetch (e.g. a
                    # delisting) - nothing to reconcile against, keep the
                    # cache as-is and just append whatever came back.
                    carried = cached_df
                combined = pd.concat([carried, fresh_df])
            else:
                combined = fresh_df

            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            previous_rows = len(cached_df) if cached_df is not None else 0
            rows_added = max(len(combined) - previous_rows, 0)

            _write_parquet(path, combined)

            # Record when this ticker's cache was last fetched/updated.
            last_fetched = read_max_timestamp(path)

            status = (
                SYNC_STATUS_CORPORATE_ACTION_ADJUSTED
                if corporate_action is not None
                else SYNC_STATUS_SYNCED
            )
            return SyncResult(
                ticker=ticker,
                status=status,
                rows_added=rows_added,
                corporate_action=corporate_action,
                last_fetched_at=last_fetched,
            )
        except Exception as exc:
            # Isolated per ticker on purpose - see the docstring above.
            existing = (
                read_max_timestamp(self.path_for(ticker))
                if self.path_for(ticker).exists()
                else None
            )
            return SyncResult(
                ticker=ticker,
                status=SYNC_STATUS_ERROR,
                error=str(exc),
                last_fetched_at=existing,
            )

    def sync_universe(
        self, tickers: list[str], now: pd.Timestamp | None = None
    ) -> list[SyncResult]:
        """`sync_ticker` for every ticker in `tickers`, in order."""
        return [self.sync_ticker(ticker, now) for ticker in tickers]
