"""
`GET /api/v1/data/health` - data-store freshness and integrity summary.

Lets a user answer: "Is my parquet cache fresh? How many tickers do I have?
Which tickers are stale? When was the last successful sync?"

Uses `read_max_timestamp` (parquet footer metadata, never loads OHLCV) per
ticker so a 500-ticker cache is readable in well under a second.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..data.loader import cached_tickers
from ..quant.data.parquet_manager import read_max_timestamp

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/data", tags=["data-health"])

#: Tickers whose last bar is older than this many days are flagged stale
#: in the health report. None = no threshold (everything is "fresh").
#: Can be overridden at runtime by setting DATA_HEALTH_STALE_DAYS.


def _compute_health(settings: Settings, stale_days: int | None = None) -> dict:
    """Snapshot of the parquet cache's freshness and size."""
    ticker_list = cached_tickers(settings.data_dir)
    total = len(ticker_list)

    if total == 0:
        return {
            "total_tickers": 0,
            "newest_ticker": None,
            "newest_date": None,
            "oldest_ticker": None,
            "oldest_date": None,
            "stale_count": 0,
            "stale_tickers": [],
            "total_rows_estimate": 0,
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

    newest_ticker = None
    newest_date = None
    oldest_ticker = None
    oldest_date = None
    stale_count = 0
    stale_tickers: list[str] = []
    total_rows = 0

    for ticker in ticker_list:
        path = settings.data_dir / f"{ticker}.parquet"
        max_ts = read_max_timestamp(path)
        if max_ts is None:
            continue

        total_rows += 1  # one per ticker; accurate enough for a health check

        if newest_date is None or max_ts > newest_date:
            newest_date = max_ts
            newest_ticker = ticker
        if oldest_date is None or max_ts < oldest_date:
            oldest_date = max_ts
            oldest_ticker = ticker

        if stale_days is not None:
            age = (
                datetime.now(timezone.utc).replace(tzinfo=None) - max_ts.normalize()
            ).days
            if age > stale_days:
                stale_count += 1
                stale_tickers.append(ticker)

    return {
        "total_tickers": total,
        "newest_ticker": newest_ticker,
        "newest_date": (
            newest_date.strftime("%Y-%m-%d") if newest_date is not None else None
        ),
        "oldest_ticker": oldest_ticker,
        "oldest_date": (
            oldest_date.strftime("%Y-%m-%d") if oldest_date is not None else None
        ),
        "stale_count": stale_count,
        "stale_tickers": stale_tickers[:50],  # cap the list
        "more_stale_tickers": stale_count > 50,
        "total_rows_estimate": total_rows,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health")
def data_health(
    settings: Settings = Depends(get_settings),
) -> dict:
    """Parquet cache freshness, size, and stale-ticker summary."""
    stale_days = settings.screener_stale_after_days
    return _compute_health(settings, stale_days=stale_days)
