"""Cycle 06 backtests 1 and 3, rules pre-registered in
docs/research/strategy-evidence-2026-11.md (no tuning).

1. SPY intraday momentum (Gao, Han, Li & Zhou 2018): the return from the
   previous regular-session close to 10:00 New York sets the side of a trade
   held over the last 30 minutes of the session.
2. Sector-ETF momentum (Moskowitz & Grinblatt 1999; Moskowitz, Ooi &
   Pedersen 2012): monthly top-3 by 6-month return (rule A) and long every
   sector with a positive 12-month return (rule B).

Pure functions only; fetching lives in scripts/run_etf_strategies.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

NY = "America/New_York"
BAR = pd.Timedelta("30min")
TRADING_DAYS = 252


# --------------------------------------------------------------------------
# Caches
# --------------------------------------------------------------------------


def last_complete_end(now: pd.Timestamp) -> pd.Timestamp:
    """Midnight New York of `now`'s NY day: fetch up to here so no bar from
    an unfinished session is ever cached."""
    return now.tz_convert(NY).normalize()


def cached_frame(
    path: Path, fetch: Callable[[], pd.DataFrame], refresh: bool = False
) -> pd.DataFrame:
    """Read `path` if cached, else fetch and write it. Empty fetches raise
    rather than cache nothing."""
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    df = fetch()
    if df.empty:
        raise ValueError(f"fetch returned no rows for {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


# --------------------------------------------------------------------------
# 1. SPY intraday momentum
# --------------------------------------------------------------------------


def session_legs(bars: pd.DataFrame, calendar: pd.DataFrame) -> pd.DataFrame:
    """Per-session prices from 30-minute bars (index = NY bar start).

    `calendar` is indexed by session date with a naive NY `close` time.
    The session close is the close of the bar starting 30 minutes before the
    calendar close, so early-close days trade 12:30-13:00. The 10:00 price
    is the close of the 09:30 bar. Days missing either bar are dropped; the
    first session has no previous close and is dropped too.
    """
    ts = bars.index.tz_convert(NY)
    b = pd.DataFrame(
        {"open": bars["open"].to_numpy(), "close": bars["close"].to_numpy()},
        index=ts.tz_localize(None),
    )
    days = calendar.index.normalize()
    close_t = pd.DatetimeIndex(calendar["close"])
    last = b.reindex(close_t - BAR)
    first = b.reindex(days + pd.Timedelta("9h30min"))
    out = pd.DataFrame(
        {
            "p10": first["close"].to_numpy(),
            "last_open": last["open"].to_numpy(),
            "last_close": last["close"].to_numpy(),
            "early_close": (close_t - days) < pd.Timedelta("16h"),
        },
        index=days,
    )
    out["prev_close"] = out["last_close"].shift(1)
    out["signal"] = out["p10"] / out["prev_close"] - 1
    out["r_last"] = out["last_close"] / out["last_open"] - 1
    return out.dropna(subset=["signal", "r_last"])


def intraday_positions(
    signal: pd.Series,
    pdt: bool = False,
    long_only: bool = False,
    window: int = TRADING_DAYS,
    q: float = 0.8,
    max_trades: int = 3,
    span: int = 5,
) -> pd.Series:
    """+1/-1/0 per session. PDT: only |signal| at or above its trailing
    `window`-session `q` quantile (previous days only), at most `max_trades`
    trades in any `span` sessions, taken first come first served."""
    pos = np.sign(signal).astype(int)
    if long_only:
        pos = pos.clip(lower=0)
    if not pdt:
        return pos
    a = signal.abs()
    thr = a.shift(1).rolling(window, min_periods=window).quantile(q)
    cand = (pos != 0) & (a >= thr)
    taken = np.zeros(len(pos), dtype=int)
    for i, ok in enumerate(cand.to_numpy()):
        lo = max(0, i - span + 1)
        if ok and taken[lo:i].sum() < max_trades:
            taken[i] = 1
    return pos * taken


def strategy_returns(pos: pd.Series, r_last: pd.Series, cost: float) -> pd.Series:
    """Daily return at 100% notional: side x last-bar return less `cost` per
    side on trade days."""
    return pos * r_last - 2 * cost * pos.abs()


def break_even_cost(gross_trades: pd.Series) -> float:
    """Per-side cost at which the mean net trade is zero."""
    return float(gross_trades.mean() / 2)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def perf_metrics(daily: pd.Series, trades: pd.Series | None = None) -> dict:
    """Metrics of a daily return series; trade stats only when `trades` given."""
    eq = (1 + daily).cumprod()
    total = float(eq.iloc[-1] - 1)
    peak = eq.cummax().clip(lower=1.0)
    std = daily.std(ddof=1)
    yearly = (1 + daily).groupby(daily.index.year).prod() - 1
    out = {
        "start": str(daily.index[0].date()),
        "end": str(daily.index[-1].date()),
        "total_return": total,
        "cagr": float((1 + total) ** (TRADING_DAYS / len(daily)) - 1),
        "max_drawdown": float((eq / peak - 1).min()),
        "sharpe": float(daily.mean() / std * np.sqrt(TRADING_DAYS)) if std else None,
        "pct_positive_years": float((yearly > 0).mean()),
        "trades": None,
        "hit_rate": None,
        "avg_bp": None,
    }
    if trades is not None:
        out["trades"] = int(len(trades))
        if len(trades):
            out["hit_rate"] = float((trades > 0).mean())
            out["avg_bp"] = float(trades.mean() * 1e4)
    return out


# --------------------------------------------------------------------------
# 3. Sector-ETF momentum
# --------------------------------------------------------------------------


def month_end_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Last session of each month; the final, possibly unfinished month and
    any date with no next session to trade at are excluded."""
    nxt = index[1:]
    return index[:-1][index[:-1].month != nxt.month]


def _equal(mask: pd.DataFrame) -> pd.DataFrame:
    m = mask.astype(float)
    n = m.sum(axis=1).replace(0, np.nan)
    return m.div(n, axis=0).fillna(0.0)


def top_n_weights(
    closes: pd.DataFrame, dates: pd.DatetimeIndex, lookback: int = 126, n: int = 3
) -> pd.DataFrame:
    """Rule A: equal weight on the top `n` by `lookback`-bar return; a sector
    needs `lookback` + 1 bars to be eligible."""
    ret = (closes / closes.shift(lookback) - 1).loc[dates]
    rank = ret.rank(axis=1, ascending=False, method="first")
    return _equal(rank <= n)


def abs_momentum_weights(
    closes: pd.DataFrame, dates: pd.DatetimeIndex, lookback: int = 252
) -> pd.DataFrame:
    """Rule B: equal weight on every sector whose `lookback`-bar return is
    above 0 (T-bill hurdle taken as 0); all cash if none."""
    ret = (closes / closes.shift(lookback) - 1).loc[dates]
    return _equal(ret > 0)


def equal_weights(closes: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Benchmark: equal weight on every sector trading at the date."""
    return _equal(closes.loc[dates].notna())


def run_rebalanced(
    opens: pd.DataFrame, closes: pd.DataFrame, weights: pd.DataFrame, cost: float
) -> pd.Series:
    """Daily close-to-close returns of a portfolio that moves to `weights`
    (decided at each index date's close) at the next session's open, pays
    `cost` per unit of turnover and lets weights drift between rebalances.
    Equity starts in cash."""
    o = opens.to_numpy(float)
    c = closes.to_numpy(float)
    prev_c = np.vstack([np.full(c.shape[1], np.nan), c[:-1]])
    gap = np.nan_to_num(o / prev_c, nan=1.0)
    intraday = np.nan_to_num(c / o, nan=1.0)
    full = np.nan_to_num(c / prev_c, nan=1.0)
    pos = closes.index.get_indexer(weights.index) + 1
    target = dict(zip(pos, weights.reindex(columns=closes.columns).to_numpy(float)))
    h = np.zeros(c.shape[1])
    cash = 1.0
    equity = np.empty(len(c))
    for t in range(len(c)):
        w = target.get(t)
        if w is None:
            h = h * full[t]
        else:
            h = h * gap[t]
            v = h.sum() + cash
            v -= cost * np.abs(w * v - h).sum()
            h = w * v
            cash = v - h.sum()
            h = h * intraday[t]
        equity[t] = h.sum() + cash
    eq = pd.Series(equity, index=closes.index)
    return eq.pct_change().fillna(eq.iloc[0] - 1.0)
