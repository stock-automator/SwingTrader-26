"""
Small and mid caps (cycle 07b). Rules pre-registered in
`docs/research/strategy-evidence-2026-11.md`, used as written:

- universe: EDGAR-classified common stocks (`data.broad_universe`), active
  and delisted. Eligible at a month-end close: a bar that day, close >=
  `MIN_PRICE`, 20-day average dollar volume >= `MIN_ADV`, and not among the
  `EXCLUDE_TOP` largest common stocks by that dollar volume;
- signals at the month-end close, traded at the next open, held a month:
  1. reversal: the lowest decile of the 1-month (21-session) return;
  2. 6-1 momentum: the highest decile of the return from 126 to 21 sessions
     ago (`momentum.momentum_scores`);
  3. earnings drift: cycle 06's rules (`quant.earnings_drift`), with the
     month's eligible names as the member list;
  4. composite: the mean cross-sectional z-score (population sd, eligible
     names with both returns) of -1m return, 6-1 return and the latest EAR
     known within `EAR_DAYS` sessions (else 0); the top `TOP_COMPOSITE`;
- equal weight, long-only, fractional shares, cash earns 0;
- costs per side on traded notional: `COST_LIQUID` where the previous
  close's 20-day dollar volume is above `COST_TIER_ADV`, else
  `COST_ILLIQUID`; `mult` scales both (2x costs);
- a held stock with no bar is sold at its last close (delisted exit).

Data hygiene: tickers with a spliced bar (`portfolio.suspect_spike`) are
dropped (`momentum.build_panel`); a bar gap longer than `MAX_GAP` sessions
starts a new segment (Alpaca reuses symbols), and returns never reach back
across it (`segment_age`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import earnings_drift as ed
from .momentum import ADV_DAYS  # noqa: F401 (the 20-day dollar volume)
from .momentum import (
    DELISTED,
    INITIAL_CAPITAL,
    REBALANCE,
    TRADE_COLUMNS,
    MomentumResult,
    Panel,
    _window,
    build_panel,
    mar,
    momentum_scores,
    month_end_mask,
    run_stats,
)

MIN_PRICE = 5.0
MIN_ADV = 5_000_000.0
EXCLUDE_TOP = 500
COST_TIER_ADV = 20_000_000.0
COST_LIQUID = 0.0010
COST_ILLIQUID = 0.0020
R1_DAYS = 21
MOM_LOOKBACK, MOM_SKIP = 126, 21
EAR_DAYS = 60
TOP_COMPOSITE = 20
DECILE = 10
MAX_GAP = 10

#: rank-day position -> {ticker: weight of equity}.
Selector = Callable[[int], dict[str, float]]


@dataclass
class UniversePanel(Panel):
    #: Sessions since the current bar segment began (NaN before any bar).
    age: pd.DataFrame | None = None


def segment_age(close: pd.DataFrame, max_gap: int = MAX_GAP) -> pd.DataFrame:
    """Sessions since each ticker's current segment began, counting the
    first bar as 1. More than `max_gap` sessions without a bar end a
    segment; the next bar starts a new one."""
    has = close.notna().to_numpy()
    pos = np.arange(len(close), dtype=float)[:, None] * np.ones(close.shape[1])
    last_bar = pd.DataFrame(np.where(has, pos, np.nan)).ffill()
    prev_bar = last_bar.shift(1).to_numpy()
    gap = pos - prev_bar - 1
    new = has & (np.isnan(prev_bar) | (gap > max_gap))
    start = pd.DataFrame(np.where(new, pos, np.nan)).ffill().to_numpy()
    return pd.DataFrame(pos - start + 1, index=close.index, columns=close.columns)


def build_universe_panel(
    frames: dict[str, pd.DataFrame], spy: pd.DataFrame
) -> UniversePanel:
    """`momentum.build_panel` (spliced tickers dropped) plus `age`."""
    base = build_panel(frames, spy)
    fields = {k: getattr(base, k) for k in base.__dataclass_fields__}
    return UniversePanel(**fields, age=segment_age(base.close))


def return_1m(panel: UniversePanel) -> pd.DataFrame:
    r = panel.mark / panel.mark.shift(R1_DAYS) - 1
    return r.where(panel.close.notna() & (panel.age > R1_DAYS))


def return_6_1(panel: UniversePanel) -> pd.DataFrame:
    r = momentum_scores(panel.close, panel.mark, MOM_LOOKBACK, MOM_SKIP)
    return r.where(panel.age > MOM_LOOKBACK)


def eligible(
    panel: Panel,
    min_price: float = MIN_PRICE,
    min_adv: float = MIN_ADV,
    exclude_top: int | None = None,
) -> pd.DataFrame:
    """True where a ticker is eligible at that close (`exclude_top` defaults
    to `EXCLUDE_TOP`)."""
    exclude_top = EXCLUDE_TOP if exclude_top is None else exclude_top
    has = panel.close.notna()
    adv = panel.adv.where(has)
    rank = adv.rank(axis=1, ascending=False, method="first")
    return has & (panel.close >= min_price) & (adv >= min_adv) & (rank > exclude_top)


def monthly_counts(elig: pd.DataFrame) -> pd.Series:
    """Eligible names at each month-end close."""
    month_end = month_end_mask(pd.DatetimeIndex(elig.index))
    return elig[month_end].sum(axis=1).astype(int)


def count_summary(counts: pd.Series) -> dict:
    return {
        "min": int(counts.min()),
        "median": float(counts.median()),
        "max": int(counts.max()),
    }


def delisted_share(panel: Panel, elig: pd.DataFrame) -> dict:
    """Of the names eligible at any month end, how many have no bar on the
    panel's last session (delisted, acquired or otherwise gone)."""
    ever = elig[month_end_mask(pd.DatetimeIndex(elig.index))].any()
    names = ever[ever].index
    gone = int(panel.close[names].iloc[-1].isna().sum())
    total = int(len(names))
    return {
        "ever_eligible": total,
        "delisted": gone,
        "pct": float(gone / total * 100) if total else 0.0,
    }


# --- Signals ---------------------------------------------------------------------


def _weights(names) -> dict[str, float]:
    names = list(names)
    return {t: 1.0 / len(names) for t in names} if names else {}


def _decile(values: pd.Series, elig: pd.Series, lowest: bool) -> dict[str, float]:
    v = values[elig.reindex(values.index, fill_value=False) & values.notna()]
    n = max(1, len(v) // DECILE) if len(v) else 0
    picked = v.nsmallest(n) if lowest else v.nlargest(n)
    return _weights(picked.index)


def lowest_decile(values: pd.Series, elig: pd.Series) -> dict[str, float]:
    return _decile(values, elig, lowest=True)


def highest_decile(values: pd.Series, elig: pd.Series) -> dict[str, float]:
    return _decile(values, elig, lowest=False)


def equal_weight(elig: pd.Series) -> dict[str, float]:
    return _weights(elig[elig].index)


def top_n(score: pd.Series, n: int) -> dict[str, float]:
    return _weights(score.nlargest(n).index)


def _z(x: pd.Series) -> pd.Series:
    sd = x.std(ddof=0)
    return (x - x.mean()) / sd if sd > 0 else x * 0.0


def composite_score(
    r1: pd.Series, r61: pd.Series, ear: pd.Series, elig: pd.Series
) -> pd.Series:
    """Mean z-score of -r1, r61 and ear (0 when missing) over eligible names
    with both returns."""
    ok = elig & r1.notna() & r61.notna()
    names = ok[ok].index
    return (_z(-r1[names]) + _z(r61[names]) + _z(ear.reindex(names).fillna(0.0))) / 3


def latest_ear(
    table: pd.DataFrame,
    index: pd.DatetimeIndex,
    tickers: list[str],
    days: int = EAR_DAYS,
) -> pd.DataFrame:
    """Per session and ticker, the EAR of the latest event known (day +1
    close, `known`) within the last `days` sessions, else 0."""
    column = {t: j for j, t in enumerate(tickers)}
    t = table.dropna(subset=["ear"])
    t = t[t["ticker"].isin(column) & (t["known"] >= 0) & (t["known"] < len(index))]
    t = t.sort_values("known").drop_duplicates(["ticker", "known"], keep="last")
    out = np.full((len(index), len(tickers)), np.nan)
    out[t["known"].to_numpy(dtype=int), t["ticker"].map(column).to_numpy()] = t[
        "ear"
    ].to_numpy(dtype=float)
    frame = pd.DataFrame(out).ffill(limit=days - 1).fillna(0.0)
    frame.index, frame.columns = index, tickers
    return frame


# --- Costs and simulation ---------------------------------------------------------


def cost_rates(adv: pd.DataFrame, mult: float = 1.0) -> pd.DataFrame:
    """Per-side cost rate for a trade on each session, from the previous
    close's dollar volume (unknown counts as illiquid)."""
    prev = adv.shift(1)
    rate = np.where(prev > COST_TIER_ADV, COST_LIQUID, COST_ILLIQUID)
    return pd.DataFrame(rate * mult, index=adv.index, columns=adv.columns)


def simulate_monthly(
    panel: Panel,
    selector: Selector,
    start,
    end,
    mult: float = 1.0,
    rates: pd.DataFrame | None = None,
    initial_capital: float = INITIAL_CAPITAL,
) -> MomentumResult:
    """`momentum.simulate` with a per-ticker cost rate: on the session after
    each month end, trade to `selector(month end)` at the open."""
    index = panel.index
    a, b = _window(index, start, end)
    month_end = month_end_mask(index)
    opens, closes = panel.open.to_numpy(), panel.close.to_numpy()
    marks = panel.mark.to_numpy()
    rate = (cost_rates(panel.adv, mult) if rates is None else rates).to_numpy()
    tickers = panel.tickers
    column = {t: j for j, t in enumerate(tickers)}

    shares = np.zeros(len(tickers))
    cash = initial_capital
    trades: list[tuple] = []
    turnover: dict[pd.Timestamp, float] = {}
    rows: list[tuple] = []
    delisted = no_open = 0

    for i in range(a, b + 1):
        day, s = index[i], rate[i]
        for j in np.flatnonzero((shares > 0) & np.isnan(closes[i])):
            price, notional = marks[i, j], shares[j] * marks[i, j]
            cash += notional * (1 - s[j])
            trades.append(
                (day, tickers[j], "sell", shares[j], price * (1 - s[j]))
                + (notional, notional * s[j], DELISTED)
            )
            shares[j] = 0.0
            delisted += 1

        if i > 0 and month_end[i - 1]:
            weights = selector(i - 1)
            o = opens[i]
            pos = np.flatnonzero(shares > 0)
            stuck = pos[np.isnan(o[pos])]
            live = pos[~np.isnan(o[pos])]
            frozen = float(np.sum(shares[stuck] * marks[i - 1, stuck]))
            held = shares[live] * o[live]
            gross = cash + float(held.sum()) + frozen
            net = cash + float(np.sum(held * (1 - s[live]))) + frozen
            target = np.zeros(len(tickers))
            target[stuck] = shares[stuck]
            no_open += len(stuck)
            tradable = {}
            for t, w in weights.items():
                j = column[t]
                if np.isnan(o[j]):
                    no_open += j not in stuck
                    continue
                tradable[j] = w
            wanted = sum(tradable.values()) * net
            scale = min(1.0, (net - frozen) / wanted) if wanted > 0 else 0.0
            for j, w in tradable.items():
                target[j] = w * scale * net / (o[j] * (1 + s[j]))
            delta = target - shares
            buys = sells = 0.0
            for j in np.flatnonzero(delta < 0):
                notional = -delta[j] * o[j]
                cash += notional * (1 - s[j])
                sells += notional
                trades.append(
                    (day, tickers[j], "sell", -delta[j], o[j] * (1 - s[j]))
                    + (notional, notional * s[j], REBALANCE)
                )
            for j in np.flatnonzero(delta > 0):
                notional = delta[j] * o[j]
                cash -= notional * (1 + s[j])
                buys += notional
                trades.append(
                    (day, tickers[j], "buy", delta[j], o[j] * (1 + s[j]))
                    + (notional, notional * s[j], REBALANCE)
                )
            shares = target
            turnover[day] = max(buys, sells) / gross if gross > 0 else 0.0

        pos = np.flatnonzero(shares > 0)
        value = float(np.sum(shares[pos] * marks[i, pos]))
        equity = cash + value
        rows.append((day, equity, cash, value / equity if equity else 0.0, len(pos)))

    frame = pd.DataFrame(rows, columns=["date", "equity", "cash", "inv", "n"])
    frame = frame.set_index("date")
    equity = frame["equity"]
    if a > 0:
        equity = pd.concat([pd.Series([initial_capital], index=[index[a - 1]]), equity])
    return MomentumResult(
        equity=equity.rename("equity"),
        cash=frame["cash"],
        invested=frame["inv"],
        holdings=frame["n"],
        trades=pd.DataFrame(trades, columns=TRADE_COLUMNS),
        turnover=pd.Series(turnover, dtype=float),
        delisted_exits=delisted,
        no_open_skips=no_open,
    )


# --- Statistics -------------------------------------------------------------------


def stats(result: MomentumResult, traded: float | None = None) -> dict:
    """`momentum.run_stats` plus Calmar (CAGR / |max drawdown|) and annual
    one-way turnover: traded notional / 2 / mean equity / years. `traded`
    defaults to the trades' `notional`."""
    out = run_stats(result)
    out["calmar"] = round(mar(out), 3) if np.isfinite(mar(out)) else None
    equity = result.equity
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if traded is None:
        trades = result.trades
        traded = float(trades["notional"].sum()) if len(trades) else 0.0
    out["turnover_annual_x"] = (
        round(traded / 2 / float(equity.mean()) / years, 3) if years > 0 else None
    )
    return out


def event_stats(result: ed.EventResult) -> dict:
    """`stats` for an earnings-drift run (entries and exits both traded)."""
    trades = result.trades
    traded = float(
        (trades["shares"] * (trades["entry_price"] + trades["exit_price"])).sum()
    )
    as_monthly = MomentumResult(
        equity=result.equity,
        cash=result.equity * 0,
        invested=result.long_exposure + result.short_exposure,
        holdings=result.holdings,
        trades=pd.DataFrame(columns=TRADE_COLUMNS),
        turnover=pd.Series(dtype=float),
        delisted_exits=result.delisted_exits,
        no_open_skips=result.no_open_skips,
    )
    out = stats(as_monthly, traded=traded)
    out["trades"] = int(len(trades))
    out["costs"] = round(float(trades["cost"].sum()), 2) if len(trades) else 0.0
    for key in ("rebalances", "monthly_turnover_pct"):
        out.pop(key)
    return out


# --- Long-short diagnostic --------------------------------------------------------


def spread_returns(
    panel: Panel,
    long_names: Callable[[int], list[str]],
    short_names: Callable[[int], list[str]],
    start,
    end,
) -> pd.Series:
    """Gross equal-weight return of the long names minus the short names,
    from each month-end close in `start`..`end` to the next month-end close
    (a name without later bars counts at its last close)."""
    index = panel.index
    ends = np.flatnonzero(month_end_mask(index))
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    marks, column = panel.mark, {t: j for j, t in enumerate(panel.tickers)}
    values = marks.to_numpy()
    out: dict[pd.Timestamp, float] = {}
    for i, j in zip(ends[:-1], ends[1:]):
        if not lo <= index[i] <= hi:
            continue
        legs = []
        for names in (long_names(i), short_names(i)):
            cols = [column[t] for t in names]
            r = values[j, cols] / values[i, cols] - 1 if cols else np.array([])
            legs.append(float(np.nanmean(r)) if len(r) else np.nan)
        out[index[i]] = legs[0] - legs[1]
    return pd.Series(out, dtype=float)


def spread_summary(spread: pd.Series) -> dict:
    v = spread.dropna().to_numpy(dtype=float)
    sd = float(np.std(v, ddof=1)) if len(v) > 1 else 0.0
    return {
        "months": int(len(v)),
        "mean_pct": float(v.mean() * 100) if len(v) else None,
        "t": float(v.mean() / (sd / np.sqrt(len(v)))) if sd > 0 else None,
        "positive_pct": float((v > 0).mean() * 100) if len(v) else None,
    }
