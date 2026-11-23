"""USD to GBP conversion from cached `GBPUSD=X` daily closes.

`GBPUSD=X` is quoted as USD per 1 GBP, so GBP = USD / rate. Display only:
a missing rate returns None and never blocks anything.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

import pandas as pd
import yfinance as yf

from .loader import DATA_DIR, DataUnavailableError, load_prices

GBPUSD_TICKER = "GBPUSD=X"
LIVE_CACHE_TTL = timedelta(seconds=60)

#: (fetched_at, price) of the last live attempt; price None = it failed, so
#: an outage costs one provider timeout per TTL, not one per request.
_live_cache: tuple[datetime, float | None] | None = None
_offline: ContextVar[bool] = ContextVar("fx_offline", default=False)
_data_dir: ContextVar[Path | str] = ContextVar("fx_data_dir", default=DATA_DIR)


@dataclass(frozen=True)
class FxRate:
    """USD per 1 GBP, when it was quoted, and whether it is "live" or "daily"."""

    rate: float
    as_of: datetime
    source: str


@contextmanager
def fx_context(data_dir: Path | str, offline: bool = False) -> Iterator[None]:
    """Set where daily closes are read from and, if `offline`, skip live quotes."""
    tokens = (_data_dir.set(data_dir), _offline.set(offline))
    try:
        yield
    finally:
        _data_dir.reset(tokens[0])
        _offline.reset(tokens[1])


def _live_quote(now: datetime) -> FxRate | None:
    global _live_cache
    if _live_cache is not None and now - _live_cache[0] < LIVE_CACHE_TTL:
        fetched_at, cached = _live_cache
        return None if cached is None else FxRate(cached, fetched_at, "live")
    try:
        price: float | None = float(yf.Ticker(GBPUSD_TICKER).fast_info["last_price"])
    except Exception:  # noqa: BLE001 - any provider failure means fall back
        price = None
    if price is not None and (not math.isfinite(price) or price <= 0):
        price = None
    _live_cache = (now, price)
    return None if price is None else FxRate(price, now, "live")


def _daily_rate(
    as_of: date | None, data_dir: Path | str, allow_download: bool = False
) -> FxRate | None:
    try:
        bars = load_prices(
            GBPUSD_TICKER, data_dir=data_dir, allow_download=allow_download
        )
    except (DataUnavailableError, OSError):
        return None
    closes = bars["Close"].dropna()
    closes = closes[closes > 0]
    if as_of is not None:
        closes = closes[closes.index.normalize() <= pd.Timestamp(as_of)]
    if closes.empty:
        return None
    return FxRate(float(closes.iloc[-1]), closes.index[-1].to_pydatetime(), "daily")


def latest_gbp_rate(now: datetime | None = None) -> FxRate | None:
    """Latest quote (cached 60s), else the stored daily close, else None."""
    now = now or datetime.now()
    if not _offline.get():
        live = _live_quote(now)
        if live is not None:
            return live
    return _daily_rate(None, _data_dir.get())


def usd_to_gbp(
    amount: float,
    as_of: date | None = None,
    data_dir: Path | str = DATA_DIR,
    allow_download: bool = False,
) -> tuple[float, float, date] | None:
    """Convert `amount` USD to GBP at the latest rate, or the close on or before `as_of`.

    Returns `(gbp, rate, rate_date)`, or None when no usable rate exists
    (uncached, provider down, or no non-NaN close by `as_of`).
    """
    if as_of is None:
        with fx_context(data_dir):
            found = latest_gbp_rate()
    else:
        found = _daily_rate(as_of, data_dir, allow_download)
    if found is None:
        return None
    return amount / found.rate, found.rate, found.as_of.date()
