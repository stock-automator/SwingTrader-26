"""USD to GBP conversion from cached `GBPUSD=X` daily closes.

`GBPUSD=X` is quoted as USD per 1 GBP, so GBP = USD / rate. Display only:
a missing rate returns None and never blocks anything.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .loader import DATA_DIR, DataUnavailableError, load_prices

GBPUSD_TICKER = "GBPUSD=X"


def usd_to_gbp(
    amount: float,
    as_of: date | None = None,
    data_dir: Path | str = DATA_DIR,
    allow_download: bool = False,
) -> tuple[float, float, date] | None:
    """Convert `amount` USD to GBP at the latest close on or before `as_of`.

    Returns `(gbp, rate, rate_date)`, or None when no usable rate exists
    (uncached, provider down, or no non-NaN close by `as_of`).
    """
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
    rate = float(closes.iloc[-1])
    return amount / rate, rate, closes.index[-1].date()
