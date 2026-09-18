"""
Per-bar dynamic spread and volume-based market-impact model.

`engine.estimate_atr_spread_pct` prices a *whole backtest run's* spread off
one number - the dataset's mean ATR/Close ratio - because `backtesting.py`'s
`spread` parameter only accepts one constant for the entire run. That is
the right trade-off for `engine.run_backtest` (see its own docstring), but
it is not what `api/replay.py`'s `simulate-trade-execution` needs: that
endpoint prices *one specific trade* on *one specific bar*, and has no
"whole run" to average over. This module is the single-trade equivalent -
spread priced off that bar's own ATR, not the dataset average - plus a
market-impact term the whole-run engine has no equivalent of at all: cost
that scales with how large an order is relative to the stock's own trading
volume, not just the stock's volatility.

Three things a single-trade replay can report that a whole-run backtest
can't:

- `spread_pct` - this bar's ATR-implied bid/ask spread.
- `market_impact_pct` - added cost from the order being large relative to
  the ticker's own trailing volume, via a square-root participation-rate
  model (`impact ~ sqrt(order_size / avg_volume)`) - a standard practitioner
  approximation: doubling participation roughly 1.4x's impact, not 2x's it,
  because the first shares of a large order are easier to fill than the
  last.
- `spread_variance_pct` - how much the ATR-implied spread itself has moved
  over the trailing window, i.e. how much confidence to put in `spread_pct`
  as a single number rather than a range.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import wilder_atr

#: Trailing bars `trailing_avg_volume`/`spread_variance_pct` look back over
#: by default - the same 20-bar convention `api.config.Settings.
#: screener_volume_lookback` already uses elsewhere in this codebase.
DEFAULT_LOOKBACK = 20

#: Hard ceiling on modelled market impact, regardless of how large an order
#: or how illiquid a name - a runaway `sqrt` blowup on a near-zero-volume
#: ticker must not produce a nonsensical fill price.
MAX_MARKET_IMPACT_PCT = 0.05


@dataclass(frozen=True)
class SlippageBreakdown:
    """Cost components of a single simulated fill, each as a fraction of
    the reference price (e.g. `0.001` = 0.1%)."""

    spread_pct: float
    market_impact_pct: float
    spread_variance_pct: float

    @property
    def total_slippage_pct(self) -> float:
        return self.spread_pct + self.market_impact_pct

    def as_dict(self) -> dict:
        return {
            "spread_pct": round(self.spread_pct, 6),
            "market_impact_pct": round(self.market_impact_pct, 6),
            "spread_variance_pct": round(self.spread_variance_pct, 6),
            "total_slippage_pct": round(self.total_slippage_pct, 6),
        }


def estimate_dynamic_spread_pct(
    df: pd.DataFrame, idx: int, atr_multiple: float = 0.1
) -> float:
    """This specific bar's ATR-implied bid/ask spread, as a fraction of price.

    Unlike `engine.estimate_atr_spread_pct` (the dataset-wide mean, priced
    once for a whole backtest run), this reads the ATR/Close ratio at `idx`
    alone - a volatile stretch of a ticker's history gets a wider modelled
    spread than a quiet stretch of the *same* ticker, which a single
    constant cannot represent.

    Args:
        idx: Positional row index into `df` (e.g. the fill bar).
        atr_multiple: Fraction of the bar's ATR/Close ratio charged as
            spread - same meaning and default as `engine.
            estimate_atr_spread_pct`'s `atr_multiple`.

    Returns:
        `0.0` if the bar's ATR is still in its warm-up window (NaN) or
        non-finite - never NaN or inf.

    Raises:
        ValueError: if `atr_multiple` is negative, or `idx` is out of range.
    """
    if atr_multiple < 0:
        raise ValueError("atr_multiple must be non-negative")
    if not 0 <= idx < len(df):
        raise ValueError(f"idx {idx} out of range for a {len(df)}-row frame")

    atr = wilder_atr(df)
    close = float(df["Close"].iloc[idx])
    atr_value = float(atr.iloc[idx])
    if not math.isfinite(atr_value) or close <= 0:
        return 0.0

    spread = (atr_value / close) * atr_multiple
    return spread if math.isfinite(spread) and spread > 0 else 0.0


def spread_variance_pct(
    df: pd.DataFrame,
    idx: int,
    atr_multiple: float = 0.1,
    lookback: int = DEFAULT_LOOKBACK,
) -> float:
    """Standard deviation of the ATR-implied spread over the `lookback` bars
    ending at (and including) `idx` - how much `estimate_dynamic_spread_pct`
    itself has moved recently, i.e. how much to trust it as a point estimate.

    Returns `0.0` (not NaN) if fewer than 2 bars are available in the
    window, or every value in it is non-finite.
    """
    if lookback < 1:
        raise ValueError("lookback must be at least 1")
    if not 0 <= idx < len(df):
        raise ValueError(f"idx {idx} out of range for a {len(df)}-row frame")

    atr = wilder_atr(df)
    start = max(0, idx - lookback + 1)
    end = idx + 1
    window_atr = atr.iloc[start:end]
    window_close = df["Close"].iloc[start:end]

    ratio = (window_atr / window_close).replace([np.inf, -np.inf], np.nan).dropna()
    if len(ratio) < 2:
        return 0.0

    variance = float((ratio * atr_multiple).std(ddof=0))
    return variance if math.isfinite(variance) else 0.0


def trailing_avg_volume(
    df: pd.DataFrame, idx: int, lookback: int = DEFAULT_LOOKBACK
) -> float:
    """Mean `Volume` over the `lookback` bars ending at (and including)
    `idx`. Returns `0.0` if the window is empty or averages non-finite."""
    if lookback < 1:
        raise ValueError("lookback must be at least 1")
    if not 0 <= idx < len(df):
        raise ValueError(f"idx {idx} out of range for a {len(df)}-row frame")

    start = max(0, idx - lookback + 1)
    end = idx + 1
    volume = float(df["Volume"].iloc[start:end].mean())
    return volume if math.isfinite(volume) and volume > 0 else 0.0


def estimate_market_impact_pct(
    shares: float,
    avg_volume: float,
    impact_coefficient: float = 0.1,
    max_impact_pct: float = MAX_MARKET_IMPACT_PCT,
) -> float:
    """Adverse price impact from `shares` being large relative to
    `avg_volume`, via a square-root participation-rate model.

    `impact = impact_coefficient * sqrt(shares / avg_volume)`, capped at
    `max_impact_pct`. `0.0` whenever `shares` or `avg_volume` is
    non-positive - no volume history means no impact signal to price, not
    infinite impact.

    Args:
        impact_coefficient: Scales the whole curve, e.g. `0.1` -> an order
            equal to 100% of trailing average volume costs 10% impact
            before the cap. `0.0` disables market-impact modelling entirely
            (the default in `SimulateTradeExecutionRequest`, so existing
            callers see no behavior change unless they opt in).

    Raises:
        ValueError: if `impact_coefficient` or `max_impact_pct` is negative.
    """
    if impact_coefficient < 0:
        raise ValueError("impact_coefficient must be non-negative")
    if max_impact_pct < 0:
        raise ValueError("max_impact_pct must be non-negative")
    if impact_coefficient == 0 or shares <= 0 or avg_volume <= 0:
        return 0.0

    participation = shares / avg_volume
    impact = impact_coefficient * math.sqrt(participation)
    return min(impact, max_impact_pct)


def estimate_execution_drag(
    df: pd.DataFrame,
    idx: int,
    shares: float,
    atr_multiple: float = 0.1,
    impact_coefficient: float = 0.0,
    volume_lookback: int = DEFAULT_LOOKBACK,
    max_impact_pct: float = MAX_MARKET_IMPACT_PCT,
) -> SlippageBreakdown:
    """Full per-bar cost breakdown for a single simulated fill: dynamic
    spread plus volume-based market impact, and the spread's own recent
    variance for a confidence read on the estimate.

    This is the function `api.replay.simulate_trade_execution` calls; the
    three component functions above exist separately mainly so each is
    independently unit-testable.
    """
    spread = estimate_dynamic_spread_pct(df, idx, atr_multiple)
    variance = spread_variance_pct(df, idx, atr_multiple, volume_lookback)
    avg_volume = trailing_avg_volume(df, idx, volume_lookback)
    impact = estimate_market_impact_pct(
        shares, avg_volume, impact_coefficient, max_impact_pct
    )
    return SlippageBreakdown(
        spread_pct=spread, market_impact_pct=impact, spread_variance_pct=variance
    )
