"""
Earnings-announcement-return drift (cycle 06, backtest 2). Rules are the
pre-registered ones in `docs/research/strategy-evidence-2026-11.md` (Brandt
et al. 2008; Chan 2003), fixed before any run:

- day 0: the session of the 8-K Item 2.02 filing (`data.edgar.day_zero`);
- EAR: the stock's daily return minus SPY's, compounded over days -1, 0 and
  +1 (closes, so from the day -2 close to the day +1 close);
- an event counts only if the ticker is in the S&P 500 snapshot of day 0's
  month (before the first snapshot, that snapshot: trailing pool only);
- at the day +2 open the EAR is ranked against the member EARs known before
  it: those whose day +1 is among the previous `POOL_DAYS` sessions. Above
  the pool's 90th percentile is "top", below its 10th "bottom". A pool of
  fewer than `MIN_POOL` EARs classifies nothing (warm-up only);
- long-only: buy "top" events at the day +2 open, `1/MAX_POSITIONS` of the
  previous close's equity each, at most `MAX_POSITIONS` held; candidates on
  one open are taken largest EAR first, and a ticker already held is
  skipped. Long-short: also short "bottom" events (smallest EAR first), up
  to `MAX_POSITIONS` shorts. Borrow cost is not modelled;
- hold `HOLD_DAYS` sessions: exit at the close of day +61. A held stock with
  no bar (delisted, acquired, data gap) exits at its last close;
- costs `COST_PER_SIDE` of notional on every entry and exit, cash earns 0,
  fractional shares.

The event study measures each event's buy-and-hold abnormal return from the
day +2 open to the day +61 close (the 60 sessions held), minus SPY's over the
same span.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .backtest import exposure_matched_curve
from .momentum import MomentumResult, run_stats

HOLD_DAYS = 60
ENTRY_OFFSET = 2
POOL_DAYS = 252
MIN_POOL = 100
TOP_QUANTILE = 0.9
BOTTOM_QUANTILE = 0.1
MAX_POSITIONS = 20
COST_PER_SIDE = 0.0005
INITIAL_CAPITAL = 10_000.0

TOP, BOTTOM, MID = "top", "bottom", "mid"
HOLD_EXIT = "held 60 sessions"
DELISTED = "no bar (delisted or missing)"

TRADE_COLUMNS = [
    "ticker",
    "side",
    "day0",
    "ear",
    "entry_date",
    "entry_price",
    "exit_date",
    "exit_price",
    "reason",
    "shares",
    "cost",
    "return",
]


@dataclass
class Prices:
    """Bars on SPY's calendar; NaN where a ticker has no bar."""

    open: pd.DataFrame
    close: pd.DataFrame
    spy_open: pd.Series
    spy_close: pd.Series


@dataclass
class EventResult:
    equity: pd.Series
    #: (long - short) value / equity at each close.
    invested: pd.Series
    long_exposure: pd.Series
    short_exposure: pd.Series
    #: Positions alive during each session (entered by its open, exiting at
    #: or after its close).
    holdings: pd.Series
    trades: pd.DataFrame
    #: Signal rows that were entered (index into the signal table).
    entered: list
    delisted_exits: int = 0
    skipped_full: int = 0
    no_open_skips: int = 0


def member_at_day0(events: pd.DataFrame, membership: pd.DataFrame) -> np.ndarray:
    """True where the ticker is in the snapshot of day 0's month; months
    before the first snapshot use the first snapshot."""
    months = membership["date"].dt.to_period("M")
    snapshots = set(zip(membership["ticker"], months))
    day_month = pd.to_datetime(events["day0"]).dt.to_period("M")
    day_month = day_month.where(day_month >= months.min(), months.min())
    return np.array(
        [(t, m) in snapshots for t, m in zip(events["ticker"], day_month)], dtype=bool
    )


def event_table(
    events: pd.DataFrame, prices: Prices, hold_days: int = HOLD_DAYS
) -> pd.DataFrame:
    """Per event (`ticker, day0`): calendar positions `i0, known, entry,
    exit_plan, exit`, the `ear`, and the abnormal return `bhar` from the day
    +2 open to the exit close. `exit` is the planned exit, the last close
    before a missing bar (`delisted`), or the last bar of the calendar;
    `complete` is False only when the calendar ends first."""
    index = prices.close.index
    column = {t: j for j, t in enumerate(prices.close.columns)}
    ev = events[events["ticker"].isin(column)].reset_index(drop=True).copy()
    n = len(index)
    i0 = index.get_indexer(pd.to_datetime(ev["day0"]))
    j = ev["ticker"].map(column).to_numpy()
    close, opens = prices.close.to_numpy(), prices.open.to_numpy()
    spy = prices.spy_close.reindex(index).to_numpy()
    spy_open = prices.spy_open.reindex(index).to_numpy()

    ok = (i0 >= 2) & (i0 + 1 < n)
    ear = np.full(len(ev), np.nan)
    if ok.any():
        a, jj = i0[ok], j[ok]
        growth = np.ones(len(a))
        for t in (-1, 0, 1):
            stock = close[a + t, jj] / close[a + t - 1, jj] - 1
            market = spy[a + t] / spy[a + t - 1] - 1
            growth *= 1 + stock - market
        ear[ok] = growth - 1

    entry = i0 + ENTRY_OFFSET
    exit_plan = entry + hold_days - 1
    exits = np.full(len(ev), -1)
    bhar = np.full(len(ev), np.nan)
    delisted = np.zeros(len(ev), dtype=bool)
    complete = np.zeros(len(ev), dtype=bool)
    for k in np.flatnonzero((i0 >= 0) & (entry < n)):
        e, col = entry[k], j[k]
        if not np.isfinite(opens[e, col]):
            continue
        last = min(exit_plan[k], n - 1)
        gaps = np.flatnonzero(np.isnan(close[e : last + 1, col]))  # noqa: E203
        if len(gaps):
            if gaps[0] == 0:
                continue
            last, delisted[k] = e + gaps[0] - 1, True
        exits[k] = last
        complete[k] = delisted[k] or exit_plan[k] <= n - 1
        stock = close[last, col] / opens[e, col]
        bhar[k] = stock - spy[last] / spy_open[e]
    ev["i0"], ev["known"], ev["entry"] = i0, i0 + 1, entry
    ev["exit_plan"], ev["exit"], ev["ear"] = exit_plan, exits, ear
    ev["bhar"], ev["delisted"], ev["complete"] = bhar, delisted, complete
    return ev[ev["i0"] >= 0].reset_index(drop=True)


def classify(
    table: pd.DataFrame, pool_days: int = POOL_DAYS, min_pool: int = MIN_POOL
) -> pd.Series:
    """`top` / `bottom` / `mid` against the trailing pool of member EARs
    known in the `pool_days` sessions before this event's day +1; None for
    non-members, missing EARs and pools under `min_pool`."""
    out = pd.Series([None] * len(table), index=table.index, dtype=object)
    valid = table[table["member"].astype(bool) & table["ear"].notna()]
    pool = valid.sort_values("known")
    known, ears = pool["known"].to_numpy(), pool["ear"].to_numpy()
    for k, rows in valid.groupby("known").groups.items():
        lo = np.searchsorted(known, k - pool_days, side="left")
        hi = np.searchsorted(known, k, side="left")
        if hi - lo < min_pool:
            continue
        q_lo, q_hi = np.quantile(ears[lo:hi], [BOTTOM_QUANTILE, TOP_QUANTILE])
        e = table.loc[rows, "ear"]
        out.loc[rows] = np.where(e > q_hi, TOP, np.where(e < q_lo, BOTTOM, MID))
    return out


def window(index: pd.DatetimeIndex, start, end) -> tuple[int, int]:
    a = int(index.searchsorted(pd.Timestamp(start)))
    b = int(index.searchsorted(pd.Timestamp(end), side="right")) - 1
    if a > b:
        raise ValueError(f"no sessions between {start} and {end}")
    return a, b


def simulate(
    signals: pd.DataFrame,
    prices: Prices,
    a: int,
    b: int,
    long_short: bool = False,
    max_positions: int = MAX_POSITIONS,
    cost: float = COST_PER_SIDE,
    initial_capital: float = INITIAL_CAPITAL,
) -> EventResult:
    """Trade `signals` (`ticker, entry, exit_plan, ear, signal, day0`)
    entering on sessions `a`..`b`; positions still open at `b` are marked to
    its close."""
    index = prices.close.index
    tickers = list(prices.close.columns)
    column = {t: j for j, t in enumerate(tickers)}
    opens, closes = prices.open.to_numpy(), prices.close.to_numpy()
    marks = prices.close.ffill().to_numpy()
    sides = [TOP, BOTTOM] if long_short else [TOP]
    live = signals[
        signals["signal"].isin(sides)
        & signals["entry"].between(a, b)
        & signals["ticker"].isin(column)
    ]
    by_entry = {i: g for i, g in live.groupby("entry")}

    cash = initial_capital
    positions: list[dict] = []
    trades: list[tuple] = []
    entered: list = []
    rows: list[tuple] = []
    delisted = skipped = no_open = 0

    for i in range(a, b + 1):
        group = by_entry.get(i)
        if group is not None:
            value = sum(
                p["side"] * p["shares"] * marks[i - 1, p["j"]] for p in positions
            )
            size = (cash + value) / max_positions
            for label, side in ((TOP, 1), (BOTTOM, -1)):
                cands = group[group["signal"] == label]
                cands = cands.sort_values("ear", ascending=side < 0)
                slots = max_positions - sum(p["side"] == side for p in positions)
                for row_id, ev in cands.iterrows():
                    j = column[ev["ticker"]]
                    if any(p["j"] == j for p in positions):
                        continue
                    if slots <= 0:
                        skipped += 1
                        continue
                    price = opens[i, j]
                    if not np.isfinite(price):
                        no_open += 1
                        continue
                    notional = size if side < 0 else min(size, cash / (1 + cost))
                    if notional <= 0:
                        skipped += 1
                        continue
                    cash -= side * notional + notional * cost
                    positions.append(
                        dict(
                            j=j,
                            side=side,
                            shares=notional / price,
                            entry=i,
                            exit_plan=int(ev["exit_plan"]),
                            price=price,
                            ev=ev,
                        )
                    )
                    entered.append(row_id)
                    slots -= 1

        alive = len(positions)
        for p in list(positions):
            j = p["j"]
            if np.isnan(closes[i, j]):
                price, reason = marks[i, j], DELISTED
                delisted += 1
            elif i >= p["exit_plan"]:
                price, reason = closes[i, j], HOLD_EXIT
            else:
                continue
            fee = p["shares"] * price * cost
            cash += p["side"] * p["shares"] * price - fee
            ev = p["ev"]
            trades.append(
                (
                    ev["ticker"],
                    "long" if p["side"] > 0 else "short",
                    ev["day0"],
                    ev["ear"],
                    index[p["entry"]],
                    p["price"],
                    index[i],
                    price,
                    reason,
                    p["shares"],
                    fee + p["shares"] * p["price"] * cost,
                    p["side"] * (price / p["price"] - 1),
                )
            )
            positions.remove(p)

        long_value = sum(
            p["shares"] * marks[i, p["j"]] for p in positions if p["side"] > 0
        )
        short_value = sum(
            p["shares"] * marks[i, p["j"]] for p in positions if p["side"] < 0
        )
        equity = cash + long_value - short_value
        rows.append(
            (index[i], equity, long_value / equity, short_value / equity, alive)
        )

    frame = pd.DataFrame(rows, columns=["date", "equity", "long", "short", "n"])
    frame = frame.set_index("date")
    equity = frame["equity"]
    if a > 0:
        equity = pd.concat([pd.Series([initial_capital], index=[index[a - 1]]), equity])
    return EventResult(
        equity=equity.rename("equity"),
        invested=frame["long"] - frame["short"],
        long_exposure=frame["long"],
        short_exposure=frame["short"],
        holdings=frame["n"],
        trades=pd.DataFrame(trades, columns=TRADE_COLUMNS),
        entered=entered,
        delisted_exits=delisted,
        skipped_full=skipped,
        no_open_skips=no_open,
    )


def exposure_matched(
    spy_close: pd.Series, invested: pd.Series, initial_capital: float
) -> pd.Series:
    """SPY held at the strategy's invested fraction of the prior close."""
    return exposure_matched_curve(spy_close, invested, initial_capital)


def _t(values: np.ndarray) -> float | None:
    if len(values) < 2:
        return None
    sd = float(np.std(values, ddof=1))
    return float(np.mean(values) / (sd / np.sqrt(len(values)))) if sd > 0 else None


def _summary(rows: pd.DataFrame) -> dict:
    values = rows["bhar"].to_numpy(dtype=float)
    monthly = rows.groupby(pd.to_datetime(rows["day0"]).dt.to_period("M"))["bhar"]
    return {
        "n": int(len(values)),
        "mean_pct": float(np.mean(values) * 100) if len(values) else None,
        "t": _t(values),
        "months": int(monthly.ngroups),
        "t_monthly": _t(monthly.mean().to_numpy(dtype=float)),
        "positive_pct": float((values > 0).mean() * 100) if len(values) else None,
    }


def event_study(table: pd.DataFrame, start, end) -> dict:
    """Mean abnormal return (day +2 open to day +61 close, vs SPY) of the top
    and bottom deciles, events with day 0 in `start`..`end` and a full
    window. `t` treats events as independent; `t_monthly` is the t-stat of
    the monthly cohort means (events of one month overlap in time)."""
    day0 = pd.to_datetime(table["day0"])
    rows = table[
        table["complete"].astype(bool)
        & table["bhar"].notna()
        & (day0 >= pd.Timestamp(start))
        & (day0 <= pd.Timestamp(end))
    ]
    out = {label: _summary(rows[rows["signal"] == label]) for label in (TOP, BOTTOM)}
    out["all_classified"] = _summary(rows[rows["signal"].isin([TOP, BOTTOM, MID])])
    return out


def period_stats(result: EventResult) -> dict:
    """`momentum.run_stats` (CAGR, max drawdown, Sharpe, yearly returns,
    positive years, exposure) on an event-strategy run."""
    as_momentum = MomentumResult(
        equity=result.equity,
        cash=result.equity * 0,
        invested=result.long_exposure + result.short_exposure,
        holdings=result.holdings,
        trades=result.trades,
        turnover=pd.Series(dtype=float),
        delisted_exits=result.delisted_exits,
        no_open_skips=result.no_open_skips,
    )
    stats = run_stats(as_momentum)
    for key in ("rebalances", "monthly_turnover_pct"):
        stats.pop(key)
    stats["avg_net_exposure_pct"] = float(result.invested.mean() * 100)
    stats["skipped_full"] = int(result.skipped_full)
    return stats
