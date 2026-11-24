"""
Momentum sleeve: the chosen cycle 04b variant (6-1, top 10, equal weight,
top-2N hold buffer, no SPY filter, `docs/momentum.md`) held live in the
Alpaca paper account beside the swing executor.

Live rules: rank today's universe (`config/universe.json`, the price and
liquidity filters of `quant.momentum`) on the last close of the previous
month, and trade on the first NYSE trading day of the month (a missed day
catches up on a later trading day up to the 10th). The sleeve is
`momentum_sleeve_pct` of account equity, split equally across `top_n`
names. Its holdings and quantities live in their own state file
(`Settings.momentum_state_path`): the sleeve sells only its own recorded
quantity, never more than the account holds, and skips a target the swing
executor already holds. The HTTP runner is `scripts/momentum_sleeve.py`.

State file:

    {"month": "2026-12", "complete": true, "done": ["AAPL", ...],
     "holdings": {"AAPL": 1.234567, ...},
     "start": {"date": "2026-12-01", "spy": 689.81},
     "index": 1.0, "base_value": 2987.65}

`index` chains the sleeve's return across rebalances (value before a
rebalance / `base_value` after the previous one), so "return since start"
ignores the cash moved in or out when the sleeve is resized.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from ..quant.momentum import MomentumConfig, build_panel, holdings_on

#: The approved variant: 6-1, top 10, 2N hold buffer, no market filter.
SLEEVE_CONFIG = MomentumConfig(
    lookback=126, skip=21, top_n=10, buffer_mult=2, market_filter=False
)
TAG_PREFIX = "mom"
#: Alpaca's smallest fractional order is $1 of notional.
MIN_ORDER_USD = 1.0
QTY_DECIMALS = 6
#: Longest run of closed days to step over (a holiday weekend is 4).
MAX_SCAN_DAYS = 14
#: A rebalance missed on the first trading day (Mac asleep, backend down)
#: may still run on a later trading day up to this calendar day; after it,
#: the month is skipped and the sleeve waits for the next one.
CATCH_UP_LAST_DAY = 10

CloseOn = Callable[[date], datetime | None]


def order_tag(month: str, ticker: str) -> str:
    """Alpaca `client_order_id` of the sleeve's one order per ticker per
    month, e.g. `mom-2026-12-AAPL`."""
    return f"{TAG_PREFIX}-{month}-{ticker.upper()}"


def _trading(day: date, close_on: CloseOn) -> bool:
    return close_on(day) is not None


def first_trading_day(year: int, month: int, close_on: CloseOn) -> date:
    day = date(year, month, 1)
    for _ in range(MAX_SCAN_DAYS):
        if _trading(day, close_on):
            return day
        day += timedelta(days=1)
    raise RuntimeError(f"no trading day in the first {MAX_SCAN_DAYS} of {year}-{month}")


def is_first_trading_day(day: date, close_on: CloseOn) -> bool:
    return first_trading_day(day.year, day.month, close_on) == day


def in_rebalance_window(day: date, close_on: CloseOn) -> bool:
    """A trading day no later than the `CATCH_UP_LAST_DAY`th of its month."""
    return day.day <= CATCH_UP_LAST_DAY and _trading(day, close_on)


def rank_day_for(day: date, close_on: CloseOn) -> date:
    """The last trading day before the month of `day` begins."""
    rank = day.replace(day=1) - timedelta(days=1)
    for _ in range(MAX_SCAN_DAYS):
        if _trading(rank, close_on):
            return rank
        rank -= timedelta(days=1)
    raise RuntimeError(f"no trading day in the {MAX_SCAN_DAYS} days before {day}")


def next_rebalance(today: date, state: dict | None, close_on: CloseOn) -> date:
    """While this month is unfinished, its next trading day up to the
    `CATCH_UP_LAST_DAY`th (today included), else next month's first."""
    finished = bool(state and state.get("month") == f"{today:%Y-%m}")
    finished = finished and bool(state.get("complete"))
    day = today
    while not finished and day.day <= CATCH_UP_LAST_DAY:
        if _trading(day, close_on):
            return day
        day += timedelta(days=1)
    following = today.replace(day=28) + timedelta(days=4)
    return first_trading_day(following.year, following.month, close_on)


# --------------------------------------------------------------------- state


def load_state(path: Path) -> dict:
    """The sleeve's state, or `{}` when there is none yet. An unreadable
    file raises: the sleeve must not trade without knowing what it holds."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return {}
    state = json.loads(text)
    if not isinstance(state, dict):
        raise ValueError(f"{path} is not a JSON object")
    return state


def save_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


# ------------------------------------------------------------------- ranking


def rank_targets(
    frames: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    rank_day: date,
    held: set[str],
    cfg: MomentumConfig = SLEEVE_CONFIG,
) -> tuple[list[str], dict[str, float]]:
    """`cfg`'s targets on the `rank_day` close (backtest ranking code), and
    every ticker's `rank_day` close for sizing. ValueError when SPY has no
    bar on `rank_day` (the cache is behind)."""
    panel = build_panel(frames, spy)
    stamp = pd.Timestamp(rank_day)
    if stamp not in panel.index:
        raise ValueError(f"no SPY bar on the rank day {rank_day}")
    targets = holdings_on(panel, cfg, stamp, held)
    closes = panel.close.loc[stamp].dropna()
    return targets, {t: float(v) for t, v in closes.items()}


# ---------------------------------------------------------------------- plan


@dataclass(frozen=True)
class SleeveOrder:
    ticker: str
    side: str
    qty: float
    #: The rank-day close the order was sized at.
    price: float | None

    def body(self, month: str) -> dict:
        """`POST /api/v1/execution/orders` body: a fractional MARKET DAY
        order; sells are EXITs (honoured only up to the held long)."""
        return {
            "ticker": self.ticker,
            "side": self.side,
            "qty": self.qty,
            "order_type": "MARKET",
            "intent": "EXIT" if self.side == "sell" else "ENTRY",
            "client_order_id": order_tag(month, self.ticker),
        }


@dataclass
class Plan:
    sells: list[SleeveOrder] = field(default_factory=list)
    buys: list[SleeveOrder] = field(default_factory=list)
    #: Targets held outside the sleeve (the swing executor): skipped.
    conflicts: list[str] = field(default_factory=list)
    #: Sleeve names the account no longer holds: removed from the state.
    dropped: list[str] = field(default_factory=list)


def _floor(qty: float) -> float:
    scale = 10**QTY_DECIMALS
    return math.floor(qty * scale + 1e-6) / scale


def long_quantities(positions: list[dict]) -> dict[str, float]:
    held: dict[str, float] = {}
    for p in positions:
        if p.get("side", "long") == "long":
            symbol = str(p.get("symbol", "")).upper()
            held[symbol] = held.get(symbol, 0.0) + float(p.get("qty") or 0)
    return held


def plan_rebalance(
    targets: list[str],
    sleeve: dict[str, float],
    positions: list[dict],
    prices: dict[str, float],
    sleeve_value: float,
    top_n: int = SLEEVE_CONFIG.top_n,
) -> Plan:
    """Orders moving the sleeve to `sleeve_value / top_n` in each target.
    Sells never exceed min(sleeve quantity, held long); a target any
    position outside the sleeve already holds is a conflict, not a buy."""
    plan = Plan()
    longs = long_quantities(positions)
    symbols = {str(p.get("symbol", "")).upper() for p in positions}
    per_name = sleeve_value / top_n
    wanted = set(targets)

    for ticker, qty in sleeve.items():
        have = min(float(qty), longs.get(ticker, 0.0))
        if have <= 0:
            plan.dropped.append(ticker)
            continue
        if ticker not in wanted:
            plan.sells.append(SleeveOrder(ticker, "sell", have, prices.get(ticker)))
            continue
        excess = _floor(have - per_name / prices[ticker])
        if excess * prices[ticker] >= MIN_ORDER_USD:
            plan.sells.append(SleeveOrder(ticker, "sell", excess, prices[ticker]))

    for ticker in targets:
        if ticker not in sleeve and ticker in symbols:
            plan.conflicts.append(ticker)
            continue
        have = min(float(sleeve.get(ticker, 0.0)), longs.get(ticker, 0.0))
        short = _floor(per_name / prices[ticker] - have)
        if short * prices[ticker] >= MIN_ORDER_USD:
            plan.buys.append(SleeveOrder(ticker, "buy", short, prices[ticker]))
    return plan


# ----------------------------------------------------------------- reporting


def sleeve_status(state: dict, last_close: Callable[[str], float | None]) -> dict:
    """Holdings, value at the last cached closes (`None` if any is missing),
    return since the start chained through `index`, and SPY's return since
    the start (from the rank-day close before the first trade)."""
    holdings = state.get("holdings") or {}
    closes = {t: last_close(t) for t in sorted(holdings)}
    missing = [t for t, c in closes.items() if c is None]
    value = None
    if not missing:
        value = sum(float(holdings[t]) * float(c) for t, c in closes.items())
    base = float(state.get("base_value") or 0.0)
    ret = None
    if value is not None and base > 0:
        ret = float(state.get("index") or 1.0) * value / base - 1
    start = state.get("start") or {}
    spy_now, spy_start = last_close("SPY"), start.get("spy")
    spy_ret = spy_now / float(spy_start) - 1 if spy_now and spy_start else None
    return {
        "tickers": list(closes),
        "missing": missing,
        "value": value,
        "return": ret,
        "spy_return": spy_ret,
        "start": start.get("date"),
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1%}"


def _performance(status: dict) -> str:
    return (
        f"{_pct(status['return'])} since {status['start'] or 'n/a'} "
        f"vs SPY {_pct(status['spy_return'])}"
    )


def sleeve_lines(
    state: dict,
    last_close: Callable[[str], float | None],
    next_day: date,
    sleeve_pct: float,
) -> list[str]:
    """The daily brief's momentum section."""
    cfg = SLEEVE_CONFIG
    lines = [
        "",
        f"Momentum sleeve ({cfg.lookback // 21}-{cfg.skip // 21}, top {cfg.top_n}, "
        f"{sleeve_pct:.0%} of equity)",
    ]
    status = sleeve_status(state, last_close)
    if not status["tickers"]:
        lines.append("No holdings yet.")
    else:
        lines.append(
            f"Holdings ({len(status['tickers'])}): {', '.join(status['tickers'])}"
        )
        if status["value"] is None:
            missing = ", ".join(status["missing"])
            lines.append(f"Value n/a (no cached close for {missing})")
        else:
            lines.append(f"Value ${status['value']:,.2f}, {_performance(status)}")
    lines.append(f"Next rebalance {next_day:%a %d %b %Y}")
    return lines


def sleeve_line(
    state: dict, last_close: Callable[[str], float | None], next_day: date
) -> str:
    """The weekly report's one sleeve line."""
    status = sleeve_status(state, last_close)
    tail = f"next rebalance {next_day.isoformat()}"
    n = len(status["tickers"])
    if not n:
        return f"Momentum sleeve: no holdings yet, {tail}"
    value = "value n/a" if status["value"] is None else f"${status['value']:,.2f}"
    return f"Momentum sleeve: {n} holdings, {value}, {_performance(status)}, {tail}"
