"""
Shared price-derived indicators.

Single source of truth for True Range and ATR. Prior to this module the
codebase carried three divergent ATR definitions - a Wilder EWMA in
`regime.py` and two `rolling(...).mean()` simple averages in the strategies -
so the ATR a strategy priced its stop off of was not the ATR the regime
filter or the risk sizer measured volatility with. Since `sl_value` /
`tp_value` are expressed as *multiples* of ATR, that discrepancy silently
rescaled every ATR-based stop and every volatility-parity position size.

Wilder's definition is the canonical one here: it is what ADX/DMI is built
on (so the regime layer needs it), and it is the definition every charting
package means by "ATR(14)".
"""

import pandas as pd

#: Columns `true_range` and `wilder_atr` require on their input frame.
REQUIRED_COLUMNS = ("High", "Low", "Close")


def true_range(df: pd.DataFrame) -> pd.Series:
    """Wilder's True Range, indexed like `df`.

    `max(high - low, |high - prev_close|, |low - prev_close|)`. The first bar
    is NaN - it has no previous close, and substituting `high - low` there
    would understate the range of a gap-opening first bar.

    Raises:
        ValueError: if `df` is empty or missing High/Low/Close. Checked here
            so a malformed frame fails with the exception type the rest of
            the quant layer uses rather than a bare KeyError from inside the
            arithmetic.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"df is missing required column(s): {missing}")
    if df.empty:
        raise ValueError("df is empty")

    high, low, prev_close = df["High"], df["Low"], df["Close"].shift(1)

    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1, skipna=False)


def wilder_atr(
    df: pd.DataFrame, period: int = 14, min_periods: int | None = None
) -> pd.Series:
    """Wilder-smoothed Average True Range, indexed like `df`.

    Wilder's smoothing is an EWMA with `alpha = 1 / period` and no
    bias correction (`adjust=False`), which is what makes it recursive -
    `atr_t = atr_{t-1} + (tr_t - atr_{t-1}) / period` - rather than a
    windowed average.

    Args:
        df: OHLC(V) DataFrame with High/Low/Close.
        period: Wilder smoothing period, e.g. 14.
        min_periods: Bars required before the series produces a value.
            Defaults to `period`, leaving the warm-up window NaN. Callers
            must not emit ATR-priced orders on those bars - `RiskManager`
            rejects a non-finite ATR rather than sizing off a partial
            average, which is the behaviour the old `min_periods=1` ATRs
            hid by back-filling the warm-up with the frame's own mean.

    Raises:
        ValueError: if `period` is less than 2, or via `true_range` if the
            frame is empty or malformed.
    """
    if period < 2:
        raise ValueError("period must be at least 2")

    return (
        true_range(df)
        .ewm(
            alpha=1.0 / period,
            adjust=False,
            min_periods=period if min_periods is None else min_periods,
        )
        .mean()
    )


def weekly_ema(df: pd.DataFrame, span: int = 20, rule: str = "W-FRI") -> pd.Series:
    """Weekly EMA of `Close`, reindexed onto `df`'s daily index - the
    multi-timeframe confluence check new strategies gate breakouts on
    (`Close > weekly_ema(df)`).

    Look-ahead safety is the entire point of this function, not an
    afterthought: a weekly bar under `rule` (Friday-ending weeks by default)
    is only "revealed" once its resample bin is complete, so a Monday
    through Thursday bar sees the *prior* completed week's EMA via
    forward-fill - never the still-forming current week's. Computing a
    weekly EMA some other way (e.g. a naive 100-day EMA as a "weekly"
    proxy, or resampling with a label that includes the in-progress week)
    is exactly the kind of subtle look-ahead bug this indicator exists to
    avoid.

    Args:
        span: EMA span in *weeks*, e.g. 20 for a 20-week EMA.
        rule: Pandas resample rule defining a week's boundary.

    Raises:
        ValueError: if `df` is empty or missing `Close`.
    """
    if "Close" not in df.columns:
        raise ValueError("df is missing required column: 'Close'")
    if df.empty:
        raise ValueError("df is empty")
    if span < 2:
        raise ValueError("span must be at least 2")

    weekly_close = df["Close"].resample(rule).last().dropna()
    weekly_ema_values = weekly_close.ewm(
        adjust=False, span=span, min_periods=span
    ).mean()

    return (
        weekly_ema_values.reindex(df.index.union(weekly_ema_values.index))
        .ffill()
        .reindex(df.index)
        .rename("weekly_ema")
    )
