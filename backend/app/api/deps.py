"""
Shared request-time helpers: ticker universe resolution and price loading.

Kept out of the route modules so `backtest.py` and `screener.py` share one
definition of "how do we turn a list of tickers into OHLCV frames, and what
do we do when one is unavailable" rather than drifting apart.
"""

from __future__ import annotations

import logging

import pandas as pd

from ..config import Settings, get_settings
from ..data.loader import DataUnavailableError, load_prices

log = logging.getLogger(__name__)


def load_frames(
    tickers: list[str],
    settings: Settings,
    start: str | None = None,
    end: str | None = None,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Load OHLCV bars for each ticker, tolerating per-ticker failures.

    Returns:
        `(frames, warnings)` - `frames` has one entry per ticker that
        resolved successfully; `warnings` explains every ticker that didn't,
        for the API to surface rather than silently drop.
    """
    frames: dict[str, pd.DataFrame] = {}
    warnings: list[str] = []

    for ticker in tickers:
        try:
            frames[ticker] = load_prices(
                ticker,
                start=start,
                end=end,
                data_dir=settings.data_dir,
                allow_download=settings.allow_downloads,
            )
        except DataUnavailableError as exc:
            log.warning("skipping %s: %s", ticker, exc)
            warnings.append(str(exc))

    return frames, warnings


def load_watchlist(settings: Settings) -> list[str]:
    """Tickers from `settings.watchlist_path`, one per line, capped at
    `settings.screener_max_tickers`.

    Returns an empty list if the file doesn't exist rather than raising -
    the screener endpoint reports that as an empty result set, not a 500.
    """
    path = settings.watchlist_path
    if not path.exists():
        return []

    tickers = [
        line.strip().upper()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return tickers[: settings.screener_max_tickers]


__all__ = ["load_frames", "load_watchlist", "get_settings"]
