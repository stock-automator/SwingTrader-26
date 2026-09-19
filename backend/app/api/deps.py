"""
Shared request-time helpers: ticker universe resolution and price loading.

Kept out of the route modules so `backtest.py` and `screener.py` share one
definition of "how do we turn a list of tickers into OHLCV frames, and what
do we do when one is unavailable" rather than drifting apart.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from ..config import Settings, get_settings
from ..data.loader import DataUnavailableError, load_prices

log = logging.getLogger(__name__)

#: Fetch attempts per ticker before giving up. Only retried when a download
#: was actually attempted - a pure cache miss will not change between
#: attempts, so retrying it just delays the response for free.
RETRY_ATTEMPTS = 3

#: Base delay before the first retry; doubled on each subsequent attempt
#: (0.5s, 1s) - long enough to ride out a transient provider rate limit,
#: short enough that three failed tickers don't noticeably slow a scan.
RETRY_BACKOFF_SECONDS = 0.5


def _load_one(
    ticker: str,
    settings: Settings,
    start: str | None,
    end: str | None,
) -> tuple[str, pd.DataFrame | None, Exception | None]:
    """Load one ticker's bars, retrying transient failures with backoff.

    Runs on a worker thread (see `load_frames`) - safe because `load_prices`
    only reads a parquet file or makes an independent HTTP call, with no
    shared mutable state.
    """
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            df = load_prices(
                ticker,
                start=start,
                end=end,
                data_dir=settings.data_dir,
                allow_download=settings.allow_downloads,
            )
            return ticker, df, None
        except DataUnavailableError as exc:
            last_exc = exc
            # Downloads off means every failure is a cache miss, which is
            # deterministic - retrying it would just burn the backoff delay
            # for the same answer three times.
            if not settings.allow_downloads or attempt == RETRY_ATTEMPTS - 1:
                break
            time.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
        except Exception as exc:  # noqa: BLE001 - see module docstring
            # A delisted/broken symbol can surface as something other than
            # DataUnavailableError - e.g. a malformed provider frame raising
            # ValueError deep inside pandas. This runs on a worker thread
            # inside a ThreadPoolExecutor (see `load_frames`): an
            # unretried exception here would propagate out of
            # `future.result()` and abort the *entire* batch scan over one
            # bad ticker. Not retried - the failure is structural (bad
            # frame shape), not transient, so retrying would just repeat it.
            last_exc = exc
            log.warning("unexpected error loading %s: %s", ticker, exc)
            break

    return ticker, None, last_exc


def load_frames(
    tickers: list[str],
    settings: Settings,
    start: str | None = None,
    end: str | None = None,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Load OHLCV bars for each ticker concurrently, tolerating per-ticker
    failures.

    Tickers are fetched from a bounded thread pool
    (`settings.screener_max_workers` workers) rather than one at a time -
    serially fetching a several-hundred-name watchlist is what previously
    made a full scan slow enough to look like it was skipping symbols. The
    pool size doubles as the de facto rate limit against the underlying data
    provider. Each ticker gets up to `RETRY_ATTEMPTS` tries with exponential
    backoff before it's reported as unavailable.

    Returns:
        `(frames, warnings)` - `frames` has one entry per ticker that
        resolved successfully; `warnings` explains every ticker that didn't,
        for the API to surface rather than silently drop.
    """
    frames: dict[str, pd.DataFrame] = {}
    warnings: list[str] = []

    if not tickers:
        return frames, warnings

    workers = max(1, min(settings.screener_max_workers, len(tickers)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_load_one, ticker, settings, start, end) for ticker in tickers
        ]
        for future in as_completed(futures):
            ticker, df, exc = future.result()
            if exc is not None:
                log.warning("skipping %s: %s", ticker, exc)
                warnings.append(str(exc))
            else:
                frames[ticker] = df

    return frames, warnings


def _read_watchlist(settings: Settings) -> list[str]:
    """Every ticker in `settings.watchlist_path`, one per line, uncapped.
    Empty if the file doesn't exist."""
    path = settings.watchlist_path
    if not path.exists():
        return []

    return [
        line.strip().upper()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def load_watchlist(settings: Settings) -> list[str]:
    """Tickers from `settings.watchlist_path`, one per line, capped at
    `settings.screener_max_tickers`.

    Returns an empty list if the file doesn't exist rather than raising -
    the screener endpoint reports that as an empty result set, not a 500.
    Use `watchlist_overflow` alongside this to detect (and report) silent
    truncation against the cap.
    """
    return _read_watchlist(settings)[: settings.screener_max_tickers]


def watchlist_overflow(settings: Settings) -> int:
    """How many tickers `load_watchlist` silently drops off the end of
    `settings.watchlist_path` for exceeding `screener_max_tickers`.

    `0` if the file fits under the cap or doesn't exist. Callers use this to
    surface a truncation warning instead of a scan that comes back short for
    no visible reason.
    """
    return max(0, len(_read_watchlist(settings)) - settings.screener_max_tickers)


__all__ = ["load_frames", "load_watchlist", "watchlist_overflow", "get_settings"]
