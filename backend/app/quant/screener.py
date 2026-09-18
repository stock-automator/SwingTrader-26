"""
Relative-strength factor screener, plus the earnings-catalyst blackout
filter.

`relative_strength`/`RelativeStrengthScreener` rank a universe of tickers by
their price return over a lookback window relative to a benchmark's return
over the same window (e.g. each stock vs. SPY) - a simple, standard
relative-strength momentum factor for narrowing a large universe down to
the names actually outperforming the market.

`CatalystFilter` suppresses a setup going into an unknown-outcome event: a
long entered the day before an earnings print is a bet on the print, not on
the technical setup, so it is blocked within a trading-day window of the
next known earnings date (`data.loader.fetch_earnings_dates` supplies the
dates; this module never touches the network itself).
"""

import math

import pandas as pd

#: Default trading-day window before an earnings print during which a new
#: long setup is suppressed.
DEFAULT_EARNINGS_BLACKOUT_DAYS = 5


def _pct_return(df: pd.DataFrame, lookback: int) -> float:
    """Fractional `Close`-to-`Close` return over the trailing `lookback` bars.

    Assumes the caller has already checked `len(df) >= lookback + 1`.

    Raises:
        ValueError: if either endpoint close is not positive and finite. A
            gap-filled or halted symbol can carry a 0.0 or NaN close, and
            numpy float division does not raise on those - it yields `inf`
            or `NaN`, which would then sort to the top of a ranking whose
            entire purpose is picking what to trade.
    """
    anchor = float(df["Close"].iloc[-lookback - 1])
    latest = float(df["Close"].iloc[-1])

    for label, price in (("anchor", anchor), ("latest", latest)):
        if not math.isfinite(price) or price <= 0:
            raise ValueError(
                f"{label} close must be positive and finite, got {price!r}"
            )

    return latest / anchor - 1


def relative_strength(
    asset: pd.DataFrame, benchmark: pd.DataFrame, lookback: int = 63
) -> float:
    """Asset's return over `lookback` bars minus the benchmark's return.

    Args:
        asset: OHLCV DataFrame for the ticker being scored, must have at
            least `lookback + 1` rows and a `Close` column.
        benchmark: OHLCV DataFrame for the benchmark index, same requirement.
        lookback: Number of bars to measure the return over.

    Returns:
        `asset_return - benchmark_return` as a fraction (e.g. `0.05` means
        the asset outperformed the benchmark by 5 percentage points).

    Raises:
        ValueError: if either DataFrame has fewer than `lookback + 1` rows.
    """
    if lookback < 1:
        raise ValueError("lookback must be at least 1")
    if len(asset) < lookback + 1:
        raise ValueError(f"asset has {len(asset)} rows, need at least {lookback + 1}")
    if len(benchmark) < lookback + 1:
        raise ValueError(
            f"benchmark has {len(benchmark)} rows, need at least {lookback + 1}"
        )

    return _pct_return(asset, lookback) - _pct_return(benchmark, lookback)


class RelativeStrengthScreener:
    """Ranks a ticker universe by relative-strength momentum vs. a benchmark.

    Args:
        benchmark: OHLCV DataFrame for the benchmark index (e.g. SPY).
        lookback: Number of bars to measure returns over.

    Raises:
        ValueError: if `lookback` is less than 1.
    """

    def __init__(self, benchmark: pd.DataFrame, lookback: int = 63):
        if lookback < 1:
            raise ValueError("lookback must be at least 1")

        self.benchmark = benchmark
        self.lookback = lookback

    def rank(self, universe: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Rank `universe` by relative strength, strongest first.

        Tickers are silently dropped - rather than raising - when they have
        fewer than `lookback + 1` rows, or a non-positive/non-finite close
        at either endpoint of the window (a halted or gap-filled symbol).
        A screener is expected to run over a heterogeneous universe, and
        dropping an unscoreable name is the documented behavior; emitting
        it with an `inf`/`NaN` score would put garbage at rank 1.

        A bad *benchmark* is not recoverable that way - it invalidates every
        score - so that raises.

        Returns:
            DataFrame with columns `ticker`, `asset_return`,
            `benchmark_return`, `relative_strength`, `rank` (1 = strongest),
            sorted descending by `relative_strength`.

        Raises:
            ValueError: if the benchmark has fewer than `lookback + 1` rows,
                or its window endpoints are not positive and finite.
        """
        if len(self.benchmark) < self.lookback + 1:
            raise ValueError(
                f"benchmark has {len(self.benchmark)} rows, "
                f"need at least {self.lookback + 1}"
            )

        # Identical for every ticker - compute once, not per row.
        benchmark_return = _pct_return(self.benchmark, self.lookback)

        rows = []
        for ticker, df in universe.items():
            if len(df) < self.lookback + 1:
                continue

            try:
                asset_return = _pct_return(df, self.lookback)
            except ValueError:
                # Unscoreable price data - dropped like an under-length
                # ticker, per this method's documented contract.
                continue

            rows.append(
                {
                    "ticker": ticker,
                    "asset_return": asset_return,
                    "benchmark_return": benchmark_return,
                    "relative_strength": asset_return - benchmark_return,
                }
            )

        result = pd.DataFrame(
            rows,
            columns=["ticker", "asset_return", "benchmark_return", "relative_strength"],
        )
        result = result.sort_values("relative_strength", ascending=False).reset_index(
            drop=True
        )
        result["rank"] = result.index + 1
        return result


class CatalystFilter:
    """Blocks new long setups within a trading-day window of a known
    earnings date.

    Trading-day distance is approximated with a Mon-Fri business-day count
    (`pandas.bdate_range`) rather than a real market-holiday calendar - close
    enough for a 5-day blackout window, and it keeps this filter runnable
    with no external calendar dependency or network access.

    Args:
        blackout_days: Trading days before (and including) an earnings date
            during which a new long setup is suppressed, e.g. `5`.

    Raises:
        ValueError: if `blackout_days` is negative.
    """

    def __init__(self, blackout_days: int = DEFAULT_EARNINGS_BLACKOUT_DAYS):
        if blackout_days < 0:
            raise ValueError("blackout_days must be non-negative")
        self.blackout_days = blackout_days

    def trading_days_to_next_earnings(
        self, as_of: pd.Timestamp, earnings_dates: list[pd.Timestamp]
    ) -> int | None:
        """Business-day distance from `as_of` to the next earnings date on
        or after it, or `None` if none of `earnings_dates` is upcoming.

        `0` means earnings is today/`as_of` itself.
        """
        as_of = pd.Timestamp(as_of).normalize()
        upcoming = sorted(
            d
            for d in (pd.Timestamp(x).normalize() for x in earnings_dates)
            if d >= as_of
        )
        if not upcoming:
            return None

        next_date = upcoming[0]
        if next_date == as_of:
            return 0
        return len(pd.bdate_range(start=as_of, end=next_date)) - 1

    def is_blocked(
        self, as_of: pd.Timestamp, earnings_dates: list[pd.Timestamp]
    ) -> bool:
        """Whether a long entered at `as_of` falls inside the blackout
        window of any date in `earnings_dates`."""
        distance = self.trading_days_to_next_earnings(as_of, earnings_dates)
        return distance is not None and distance <= self.blackout_days
