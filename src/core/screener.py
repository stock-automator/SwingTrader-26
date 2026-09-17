"""
Relative-strength factor screener.

Ranks a universe of tickers by their price return over a lookback window
relative to a benchmark's return over the same window (e.g. each stock vs.
SPY) - a simple, standard relative-strength momentum factor used to narrow
a large universe down to the names actually outperforming the market.
"""

import pandas as pd


def _pct_return(df: pd.DataFrame, lookback: int) -> float:
    """Fractional `Close`-to-`Close` return over the trailing `lookback` bars.

    Assumes the caller has already checked `len(df) >= lookback + 1`.
    """
    return float(df["Close"].iloc[-1] / df["Close"].iloc[-lookback - 1] - 1)


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

        Tickers with fewer than `lookback + 1` rows are silently dropped
        (not enough history to score) rather than raising, since a
        screener is expected to run over a heterogeneous universe. An
        under-length *benchmark* is not recoverable that way - it would
        invalidate every score - so it raises instead.

        Returns:
            DataFrame with columns `ticker`, `asset_return`,
            `benchmark_return`, `relative_strength`, `rank` (1 = strongest),
            sorted descending by `relative_strength`.

        Raises:
            ValueError: if the benchmark has fewer than `lookback + 1` rows.
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

            asset_return = _pct_return(df, self.lookback)
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
