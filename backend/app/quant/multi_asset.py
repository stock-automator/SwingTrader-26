"""Cycle 07a multi-asset rules, pre-registered in
docs/research/strategy-evidence-2026-11.md ("Cycle 07 pre-registered rules"),
implemented as published with no tuning.

Every weight function takes daily `closes` (one column per ETF) and the
month-end signal `dates`, and returns target weights per date (rows sum to
1; whatever is not invested goes to the cash proxy). Monthly lookbacks
count month-end closes. Execution (next open, costs, drift) is
`etf_strategies.run_rebalanced`. Pure functions only; fetching lives in
scripts/run_multi_asset.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backend.app.quant.etf_strategies import TRADING_DAYS, perf_metrics

PRICE_COLS = ["open", "high", "low", "close"]


# --------------------------------------------------------------------------
# Signals and weights
# --------------------------------------------------------------------------


def _frame(closes: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(0.0, index=dates, columns=closes.columns)


def _to_cash(w: pd.DataFrame, cash: str) -> pd.DataFrame:
    w[cash] += 1.0 - w.sum(axis=1)
    return w


def sma_filter(
    closes: pd.DataFrame, dates: pd.DatetimeIndex, months: int = 10
) -> pd.DataFrame:
    """True where the month-end close is above the mean of the last `months`
    month-end closes (today included). False until that mean exists."""
    m = closes.loc[dates]
    return m > m.rolling(months, min_periods=months).mean()


def gtaa_weights(
    closes: pd.DataFrame,
    dates: pd.DatetimeIndex,
    assets: list[str],
    cash: str = "SHY",
    months: int = 10,
) -> pd.DataFrame:
    """Faber GTAA: 1/N per asset, held only above its 10-month SMA; each
    failing slice sits in `cash`."""
    w = _frame(closes, dates)
    w[assets] = sma_filter(closes[assets], dates, months).astype(float) / len(assets)
    return _to_cash(w, cash)


def gtaa_aggressive_weights(
    closes: pd.DataFrame,
    dates: pd.DatetimeIndex,
    assets: list[str],
    cash: str = "SHY",
    top: int = 3,
    lookbacks: tuple[int, ...] = (1, 3, 6, 12),
    months: int = 10,
) -> pd.DataFrame:
    """Faber GTAA aggressive: 1/`top` each on the top `top` assets by the mean
    of their 1/3/6/12-month returns, each held only above its 10-month SMA;
    failing or unfilled slices sit in `cash`."""
    m = closes.loc[dates, assets]
    score = sum(m / m.shift(k) - 1 for k in lookbacks) / len(lookbacks)
    picked = score.rank(axis=1, ascending=False, method="first") <= top
    w = _frame(closes, dates)
    held = picked & sma_filter(closes[assets], dates, months)
    w[assets] = held.astype(float) / top
    return _to_cash(w, cash)


def dual_momentum_weights(
    closes: pd.DataFrame,
    dates: pd.DatetimeIndex,
    us: str = "SPY",
    intl: str = "EFA",
    bonds: str = "IEF",
    cash: str = "SHY",
    lookback: int = 12,
) -> pd.DataFrame:
    """Antonacci GEM: if `us`'s 12-month return beats `cash`'s, hold the
    better of `us` and `intl` (ties to `us`); otherwise hold `bonds`. No
    12-month history counts as risk-off."""
    m = closes.loc[dates, [us, intl, cash]]
    r = m / m.shift(lookback) - 1
    on = r[us] > r[cash]
    pick_intl = r[intl] > r[us]
    w = _frame(closes, dates)
    w.loc[on & ~pick_intl, us] = 1.0
    w.loc[on & pick_intl, intl] = 1.0
    w.loc[~on, bonds] = 1.0
    return w


def inverse_vol_weights(
    closes: pd.DataFrame,
    dates: pd.DatetimeIndex,
    assets: list[str],
    window: int = 60,
    sma_months: int | None = None,
    cash: str = "SHY",
) -> pd.DataFrame:
    """Risk parity: weights proportional to 1 / std of the last `window`
    daily returns. With `sma_months`, an asset below its SMA has its weight
    moved to `cash`. All cash until every vol exists for some asset."""
    px = closes[assets]
    vol = (px / px.shift(1) - 1).rolling(window, min_periods=window).std()
    inv = 1.0 / vol.loc[dates]
    w_assets = inv.div(inv.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    if sma_months is not None:
        w_assets = w_assets.where(sma_filter(px, dates, sma_months), 0.0)
    w = _frame(closes, dates)
    w[assets] = w_assets
    return _to_cash(w, cash)


def fixed_weights(dates: pd.DatetimeIndex, weights: dict[str, float]) -> pd.DataFrame:
    """The same target weights at every date (benchmarks, permanent portfolio)."""
    return pd.DataFrame([weights] * len(dates), index=dates, dtype=float)


def yearly_dates(dates: pd.DatetimeIndex, start: pd.Timestamp) -> pd.DatetimeIndex:
    """`start` plus every December signal date after it: the first entry and
    then one rebalance a year."""
    d = dates[dates >= start]
    return d[(d == start) | (d.month == 12)]


# --------------------------------------------------------------------------
# Turnover and metrics
# --------------------------------------------------------------------------


def turnover(opens: pd.DataFrame, weights: pd.DataFrame) -> pd.Series:
    """Buys plus sells as a fraction of equity at each rebalance, indexed by
    the execution session (the one after each signal date). Holdings drift
    with the opens between trades, as in `run_rebalanced`, whose cost per
    rebalance is exactly `cost` x this number. Starts from all cash."""
    pos = opens.index.get_indexer(weights.index) + 1
    o = opens.reindex(columns=weights.columns).to_numpy(float)
    target = weights.to_numpy(float)
    out = np.empty(len(pos))
    held = np.zeros(target.shape[1])
    for k, t in enumerate(pos):
        if k:
            grown = target[k - 1] * np.nan_to_num(o[t] / o[pos[k - 1]], nan=1.0)
            held = grown / (grown.sum() + 1.0 - target[k - 1].sum())
        out[k] = np.abs(target[k] - held).sum()
    return pd.Series(out, index=opens.index[pos])


def period_metrics(daily: pd.Series, turn: pd.Series | None = None) -> dict:
    """CAGR, max drawdown, Sharpe (rf = 0), Calmar, worst calendar year,
    % positive months and annual turnover of one period's daily returns.
    Edge years and months may be partial."""
    base = perf_metrics(daily)
    growth = 1 + daily
    yearly = growth.groupby(daily.index.year).prod() - 1
    monthly = growth.groupby(daily.index.to_period("M")).prod() - 1
    years = len(daily) / TRADING_DAYS
    dd = base["max_drawdown"]
    out = {k: base[k] for k in ("start", "end", "total_return", "cagr", "max_drawdown")}
    out.update(
        sharpe=base["sharpe"],
        calmar=base["cagr"] / abs(dd) if dd < 0 else None,
        worst_year=float(yearly.min()),
        worst_year_label=str(yearly.idxmin()),
        pct_positive_months=float((monthly > 0).mean()),
        annual_turnover=None,
    )
    if turn is not None:
        inside = turn[(turn.index >= daily.index[0]) & (turn.index <= daily.index[-1])]
        out["annual_turnover"] = float(inside.sum() / years)
    return out


# --------------------------------------------------------------------------
# Data: splice two vendors, check they agree
# --------------------------------------------------------------------------


def splice(early: pd.DataFrame, late: pd.DataFrame) -> pd.DataFrame:
    """`late` bars as they are, preceded by `early` bars scaled so the two
    match on `late`'s first session (which `early` must contain). Returns
    across the seam are `early`'s own."""
    first = late.index[0]
    factor = late.at[first, "close"] / early.at[first, "close"]
    pre = early.loc[early.index < first, PRICE_COLS] * factor
    return pd.concat([pre, late[PRICE_COLS]]).sort_index()


def return_agreement(
    a: pd.Series, b: pd.Series, tol: float = 0.01
) -> tuple[float, pd.DataFrame]:
    """Correlation of daily close-to-close returns on common sessions, and
    the sessions where they differ by more than `tol` (unadjusted
    distributions, bad prints)."""
    common = a.index.intersection(b.index)
    r = pd.DataFrame({"a": a.loc[common], "b": b.loc[common]})
    r = (r / r.shift(1) - 1).dropna()
    bad = r[(r["a"] - r["b"]).abs() > tol]
    return float(r["a"].corr(r["b"])), bad


def patch_bad_closes(
    a: pd.DataFrame, ref: pd.DataFrame, tol: float = 0.01
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Repair one-session bad closes in `a` against reference vendor `ref`:
    where the close ratio a/ref moves more than `tol` from the previous
    session and is back within `tol` the next, the close becomes
    ref close x previous ratio. Persistent level shifts (adjustment
    differences) are left alone. Returns the repaired frame and the days."""
    k = a["close"] / ref["close"].reindex(a.index)
    prev = k.shift(1)
    bad = ((k / prev - 1).abs() > tol) & ((k.shift(-1) / prev - 1).abs() <= tol)
    out = a.copy()
    out.loc[bad, "close"] = (ref["close"].reindex(a.index) * prev)[bad]
    return out, a.index[bad.to_numpy()]
