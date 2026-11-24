"""Gap-and-Go portfolio: daily sizing, slot limits, PDT rule and metrics.

Each session, the top `max_per_day` qualified setups by `rank_score` (gap x
first-bar relative volume, known at 09:35) get entry orders. Fills are taken
in time order, sized off the start-of-day equity: `risk_pct` of equity at
risk, at most `notional_cap` of equity per position, whole shares. All
positions close the same day. With `pdt=True` at most 3 day trades fall in
any rolling 5 sessions; later fills beyond that allowance are dropped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

QUALIFIED = ("traded", "no_trigger")
PDT_TRADES = 3
PDT_WINDOW = 5


@dataclass
class PortfolioResult:
    trades: pd.DataFrame
    #: End-of-session equity on every session.
    equity: pd.Series
    start_equity: float


def run_portfolio(
    setups: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    start_equity: float = 10_000.0,
    risk_pct: float = 0.01,
    notional_cap: float = 0.25,
    max_per_day: int = 3,
    pdt: bool = False,
    directions: tuple[str, ...] = ("long", "short"),
) -> PortfolioResult:
    s = setups[setups["status"].isin(QUALIFIED) & setups["direction"].isin(directions)]
    by_day = {d: g for d, g in s.groupby("date")} if len(s) else {}
    equity = start_equity
    curve: list[float] = []
    counts: list[int] = []
    taken: list[dict[str, Any]] = []
    for i, day in enumerate(sessions):
        n_today = 0
        pnl_today = 0.0
        g = by_day.get(day)
        if g is not None:
            allowance = max_per_day
            if pdt:
                lookback = max(0, len(counts) - PDT_WINDOW + 1)
                recent = counts[lookback:]
                allowance = min(allowance, PDT_TRADES - sum(recent))
            ordered = g.sort_values("rank_score", ascending=False, kind="stable").head(
                max_per_day
            )
            fills = ordered[ordered["status"] == "traded"].sort_values(
                "entry_time", kind="stable"
            )
            for row in fills.itertuples(index=False):
                if n_today >= allowance:
                    break
                risk_ps = abs(row.entry - row.stop)
                shares = math.floor(
                    min(equity * risk_pct / risk_ps, equity * notional_cap / row.entry)
                )
                if shares < 1:
                    continue
                sign = 1.0 if row.direction == "long" else -1.0
                pnl = shares * sign * (row.exit - row.entry)
                taken.append(
                    {
                        **row._asdict(),
                        "shares": shares,
                        "pnl": pnl,
                        "equity_before": equity,
                    }
                )
                pnl_today += pnl
                n_today += 1
        equity += pnl_today
        curve.append(equity)
        counts.append(n_today)
    cols = list(setups.columns) + ["shares", "pnl", "equity_before"]
    trades = pd.DataFrame(taken, columns=cols)
    return PortfolioResult(
        trades, pd.Series(curve, index=sessions, dtype=float), start_equity
    )


def summarize(res: PortfolioResult, spy_close: pd.Series) -> dict[str, float]:
    """Trade and curve metrics; `spy_close` covers the same sessions."""
    t = res.trades
    pnl = t["pnl"].astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    eq = pd.concat([pd.Series([res.start_equity]), res.equity.reset_index(drop=True)])
    total = float(eq.iloc[-1] / res.start_equity - 1)
    days = (
        (res.equity.index[-1] - res.equity.index[0]).days if len(res.equity) > 1 else 0
    )
    years = max(days / 365.25, 1 / 252)
    rets = eq.pct_change().dropna()
    sd = rets.std()
    yearly = res.equity.groupby(res.equity.index.year).last()
    yr_start = yearly.shift(1).fillna(res.start_equity)
    spy = spy_close.dropna()
    return {
        "trades": int(len(t)),
        "win_rate": float(len(wins) / len(t)) if len(t) else np.nan,
        "payoff": (
            float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else np.nan
        ),
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) else np.nan,
        "expectancy_r": (
            float(t["r_multiple"].astype(float).mean()) if len(t) else np.nan
        ),
        "total_return": total,
        "cagr": float((1 + total) ** (1 / years) - 1) if total > -1 else -1.0,
        "max_drawdown": float((eq / eq.cummax() - 1).min()),
        "sharpe": float(rets.mean() / sd * np.sqrt(252)) if sd > 0 else np.nan,
        "pct_positive_years": (
            float((yearly > yr_start).mean()) if len(yearly) else np.nan
        ),
        "spy_return": float(spy.iloc[-1] / spy.iloc[0] - 1) if len(spy) > 1 else np.nan,
    }
