"""
Ranked cross-sectional momentum (cycle 04).

Monthly, long-only, equal weight, one $10k account with fractional shares:

- rebalance on the last trading day of each month, using only bars up to that
  day's close; trades fill at the next session's open;
- rank = total return from `lookback` bars ago to `skip` bars ago (12-1 is
  252/21, 6-1 is 126/21), on total-return-adjusted closes;
- eligible: a bar on the rank day, close >= `min_price`, `adv_days` average
  dollar volume >= `min_dollar_volume`, and `lookback` bars of history;
- hold the top `top_n` at 1/`top_n` of equity each; a holding still ranked
  within `buffer_mult` x `top_n` is kept. Every holding is re-weighted to
  1/`top_n` at each rebalance;
- optional market filter: SPY close below its `sma_days` SMA at the rebalance
  close means cash until the next rebalance;
- slippage `slippage_pct` per side on traded notional (the fill price moves
  against the trade), cash earns nothing;
- a held ticker with no bar on a day is sold at its last available close
  (less slippage) and counted as a delisted exit.

Tickers with a spliced bar (`portfolio.suspect_spike`) are excluded.

Point-in-time mode (cycle 04b): with a `membership` list (`date, ticker`
snapshots, one per month), a ticker is eligible only on days whose month's
snapshot lists it; `trim_to_membership` drops bars after a member left.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .metrics import compute_metrics
from .portfolio import suspect_spike

REBALANCE = "rebalance"
DELISTED = "no bar (delisted or missing)"

ADV_DAYS = 20
SLIPPAGE_PCT = 0.0005
INITIAL_CAPITAL = 10_000.0

TRADE_COLUMNS = [
    "date",
    "ticker",
    "side",
    "shares",
    "price",
    "notional",
    "cost",
    "reason",
]

#: date index -> currently held tickers -> {ticker: weight of equity}.
Selector = Callable[[int, set[str]], dict[str, float]]


@dataclass(frozen=True)
class MomentumConfig:
    lookback: int = 252
    skip: int = 21
    top_n: int = 10
    buffer_mult: int = 2
    market_filter: bool = False
    sma_days: int = 200
    min_price: float = 5.0
    min_dollar_volume: float = 20_000_000.0
    adv_days: int = ADV_DAYS

    @property
    def name(self) -> str:
        label = f"{self.lookback // 21}-{self.skip // 21} N{self.top_n}"
        return label + (" SPY>SMA200" if self.market_filter else "")


VARIANTS = [
    MomentumConfig(lookback=lb, top_n=n, market_filter=f)
    for lb in (252, 126)
    for n in (10, 20)
    for f in (False, True)
]


@dataclass
class Panel:
    """Every ticker on SPY's calendar. `close`/`open` are NaN where a ticker
    has no bar; `mark` is `close` forward-filled (valuation and ranking)."""

    index: pd.DatetimeIndex
    tickers: list[str]
    open: pd.DataFrame
    close: pd.DataFrame
    mark: pd.DataFrame
    dollar_volume: pd.DataFrame
    spy_close: pd.Series
    suspect: list[str] = field(default_factory=list)
    #: True where the ticker is an index member that day; None = all.
    member: pd.DataFrame | None = None

    @property
    def adv(self) -> pd.DataFrame:
        return average_dollar_volume(self.dollar_volume, ADV_DAYS)


@dataclass
class MomentumResult:
    #: Starts at the close before the window (initial capital) when known.
    equity: pd.Series
    cash: pd.Series
    #: Long notional / equity at each close, 0-1.
    invested: pd.Series
    holdings: pd.Series
    trades: pd.DataFrame
    #: Per execution day: max(buys, sells) at the open / equity at the open.
    turnover: pd.Series
    delisted_exits: int = 0
    no_open_skips: int = 0


def average_dollar_volume(dollar_volume: pd.DataFrame, days: int) -> pd.DataFrame:
    return dollar_volume.rolling(days, min_periods=days).mean()


def membership_mask(
    membership: pd.DataFrame, index: pd.DatetimeIndex, tickers: list[str]
) -> pd.DataFrame:
    """True where `ticker` is in the snapshot of the day's calendar month."""
    months = membership["date"].dt.to_period("M")
    wide = pd.crosstab(months, membership["ticker"]).astype(bool)
    wide = wide.reindex(columns=tickers, fill_value=False)
    rows = wide.reindex(index.to_period("M"), fill_value=False)
    return pd.DataFrame(rows.to_numpy(), index=index, columns=tickers)


def trim_to_membership(
    frames: dict[str, pd.DataFrame], membership: pd.DataFrame, days: int = 45
) -> dict[str, pd.DataFrame]:
    """Members only, each cut `days` after its last snapshot: enough bars to
    sell at the next rebalance, none from a later reuse of the symbol."""
    last = membership.groupby("ticker")["date"].max() + pd.Timedelta(days=days)
    return {t: df.loc[: last[t]] for t, df in frames.items() if t in last.index}


def coverage(panel: Panel, membership: pd.DataFrame, start, end) -> dict:
    """Member-months (month-end rebalance days in `start`..`end`) with a bar
    in the panel on that day."""
    a, b = _window(panel.index, start, end)
    window = slice(a, b + 1)
    ends = panel.index[window][month_end_mask(panel.index)[window]]
    period = pd.Series(ends, index=ends.to_period("M"))
    m = membership[membership["date"].dt.to_period("M").isin(period.index)]
    days = period.reindex(m["date"].dt.to_period("M")).to_numpy()
    close = panel.close.reindex(columns=sorted(set(m["ticker"])))
    stacked = close.loc[ends].notna().stack()
    have = stacked.reindex(pd.MultiIndex.from_arrays([days, m["ticker"]]))
    have = have.fillna(False).astype(bool).to_numpy()
    priced = set(m["ticker"][have])
    total = len(m)
    return {
        "member_months": int(total),
        "with_price": int(have.sum()),
        "pct": float(have.sum() / total * 100) if total else 0.0,
        "never_priced": sorted(set(m["ticker"]) - priced),
    }


def build_panel(
    frames: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
    membership: pd.DataFrame | None = None,
) -> Panel:
    calendar = pd.DatetimeIndex(spy.index)
    suspect, opens, closes, volumes = [], {}, {}, {}
    for ticker in sorted(frames):
        df = frames[ticker]
        if suspect_spike(df["Close"]):
            suspect.append(ticker)
            continue
        df = df.reindex(calendar)
        opens[ticker], closes[ticker] = df["Open"], df["Close"]
        volumes[ticker] = df["Close"] * df["Volume"]
    tickers = list(closes)
    close = pd.DataFrame(closes, index=calendar, columns=tickers, dtype=float)
    member = None
    if membership is not None:
        member = membership_mask(membership, calendar, tickers)
    return Panel(
        index=calendar,
        tickers=tickers,
        open=pd.DataFrame(opens, index=calendar, columns=tickers, dtype=float),
        close=close,
        mark=close.ffill(),
        dollar_volume=pd.DataFrame(volumes, index=calendar, columns=tickers),
        spy_close=spy["Close"].astype(float).copy(),
        suspect=suspect,
        member=member,
    )


def month_end_mask(index: pd.DatetimeIndex) -> np.ndarray:
    """True on the last trading day of each month. The final bar is never a
    month end: its month may not be finished."""
    months = index.to_period("M")
    mask = np.asarray(months[:-1] != months[1:])
    return np.append(mask, False)


def momentum_scores(
    close: pd.DataFrame, mark: pd.DataFrame, lookback: int, skip: int
) -> pd.DataFrame:
    """Return from `lookback` bars ago to `skip` bars ago; NaN without a bar
    on the day or without `lookback` bars of history."""
    return (mark.shift(skip) / mark.shift(lookback) - 1).where(close.notna())


def eligibility(panel: Panel, cfg: MomentumConfig) -> pd.DataFrame:
    adv = average_dollar_volume(panel.dollar_volume, cfg.adv_days)
    scores = momentum_scores(panel.close, panel.mark, cfg.lookback, cfg.skip)
    ok = (
        (panel.close >= cfg.min_price) & (adv >= cfg.min_dollar_volume) & scores.notna()
    )
    return ok if panel.member is None else ok & panel.member


def select_holdings(
    scores: pd.Series,
    eligible: pd.Series,
    held: set[str],
    top_n: int,
    buffer_mult: int,
) -> list[str]:
    """Held names still ranked within `buffer_mult * top_n` (best first),
    then the best-ranked others up to `top_n`. Ties rank by ticker."""
    ok = scores[eligible.reindex(scores.index, fill_value=False) & scores.notna()]
    ranked = (
        ok.rename("score")
        .rename_axis("ticker")
        .reset_index()
        .sort_values(["score", "ticker"], ascending=[False, True])["ticker"]
        .tolist()
    )
    keep = [t for t in ranked[: top_n * buffer_mult] if t in held][:top_n]
    fill = [t for t in ranked if t not in keep][: top_n - len(keep)]
    return keep + fill


def market_ok(spy_close: pd.Series, sma_days: int) -> pd.Series:
    """SPY close >= its SMA; False until the SMA exists (fail closed)."""
    sma = spy_close.rolling(sma_days, min_periods=sma_days).mean()
    return (spy_close >= sma) & sma.notna()


def momentum_selector(panel: Panel, cfg: MomentumConfig) -> Selector:
    scores = momentum_scores(panel.close, panel.mark, cfg.lookback, cfg.skip)
    ok = eligibility(panel, cfg)
    market = market_ok(panel.spy_close.reindex(panel.index), cfg.sma_days).to_numpy()

    def select(i: int, held: set[str]) -> dict[str, float]:
        if cfg.market_filter and not market[i]:
            return {}
        names = select_holdings(
            scores.iloc[i], ok.iloc[i], held, cfg.top_n, cfg.buffer_mult
        )
        return {t: 1.0 / cfg.top_n for t in names}

    return select


def equal_weight_selector(panel: Panel) -> Selector:
    """Every ticker (member) with a bar on the rebalance day, equal weight."""
    has_bar = panel.close.notna()
    if panel.member is not None:
        has_bar &= panel.member
    has_bar = has_bar.to_numpy()
    tickers = np.array(panel.tickers)

    def select(i: int, held: set[str]) -> dict[str, float]:
        names = tickers[has_bar[i]]
        return {t: 1.0 / len(names) for t in names} if len(names) else {}

    return select


def _window(index: pd.DatetimeIndex, start, end) -> tuple[int, int]:
    a = int(index.searchsorted(pd.Timestamp(start)))
    b = int(index.searchsorted(pd.Timestamp(end), side="right")) - 1
    if a > b:
        raise ValueError(f"no bars between {start} and {end}")
    return a, b


def simulate(
    panel: Panel,
    selector: Selector,
    start,
    end,
    initial_capital: float = INITIAL_CAPITAL,
    slippage_pct: float = SLIPPAGE_PCT,
) -> MomentumResult:
    """Run `selector` from `start` to `end` inclusive. A month end on the bar
    before `start` executes at `start`'s open, so a window can begin trading
    on its first day."""
    index = panel.index
    a, b = _window(index, start, end)
    month_end = month_end_mask(index)
    opens, closes = panel.open.to_numpy(), panel.close.to_numpy()
    marks = panel.mark.to_numpy()
    tickers = panel.tickers
    column = {t: j for j, t in enumerate(tickers)}
    s = slippage_pct

    shares = np.zeros(len(tickers))
    cash = initial_capital
    trades: list[tuple] = []
    turnover: dict[pd.Timestamp, float] = {}
    rows: list[tuple] = []
    delisted = no_open = 0

    for i in range(a, b + 1):
        day = index[i]
        gone = np.flatnonzero((shares > 0) & np.isnan(closes[i]))
        for j in gone:
            price = marks[i, j]
            cash += shares[j] * price * (1 - s)
            trades.append(
                (day, tickers[j], "sell", shares[j], price * (1 - s))
                + (shares[j] * price, shares[j] * price * s, DELISTED)
            )
            shares[j] = 0.0
            delisted += 1

        if i > 0 and month_end[i - 1]:
            held = {tickers[j] for j in np.flatnonzero(shares > 0)}
            weights = selector(i - 1, held)
            o = opens[i]
            pos = np.flatnonzero(shares > 0)
            # A held name without an open cannot trade today: keep it as is,
            # valued at its last close, and size the rest around it.
            stuck = pos[np.isnan(o[pos])]
            live = pos[~np.isnan(o[pos])]
            frozen = float(np.sum(shares[stuck] * marks[i - 1, stuck]))
            gross = cash + float(np.sum(shares[live] * o[live])) + frozen
            net = cash + float(np.sum(shares[live] * o[live])) * (1 - s) + frozen
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
                target[j] = w * scale * net / (o[j] * (1 + s))
            delta = target - shares
            buys = sells = 0.0
            for j in np.flatnonzero(delta < 0):
                notional = -delta[j] * o[j]
                cash += notional * (1 - s)
                sells += notional
                trades.append(
                    (day, tickers[j], "sell", -delta[j], o[j] * (1 - s))
                    + (notional, notional * s, REBALANCE)
                )
            for j in np.flatnonzero(delta > 0):
                notional = delta[j] * o[j]
                cash -= notional * (1 + s)
                buys += notional
                trades.append(
                    (day, tickers[j], "buy", delta[j], o[j] * (1 + s))
                    + (notional, notional * s, REBALANCE)
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
        start_point = pd.Series([initial_capital], index=[index[a - 1]])
        equity = pd.concat([start_point, equity])
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


def spy_buy_hold(
    spy_close: pd.Series, start, end, initial_capital: float = INITIAL_CAPITAL
) -> pd.Series:
    """SPY held from the close before `start` (when known) to `end`."""
    a, b = _window(pd.DatetimeIndex(spy_close.index), start, end)
    lo, hi = max(a - 1, 0), b + 1
    curve = spy_close.iloc[lo:hi]
    return initial_capital * curve / curve.iloc[0]


def buy_hold_result(
    spy_close: pd.Series, start, end, initial_capital: float = INITIAL_CAPITAL
) -> MomentumResult:
    equity = spy_buy_hold(spy_close, start, end, initial_capital)
    days = equity.index[1:] if len(equity) > 1 else equity.index
    one = pd.Series(1.0, index=days)
    return MomentumResult(
        equity=equity,
        cash=one * 0,
        invested=one,
        holdings=one,
        trades=pd.DataFrame(columns=TRADE_COLUMNS),
        turnover=pd.Series(dtype=float),
    )


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def run_stats(result: MomentumResult) -> dict:
    equity = result.equity
    m = compute_metrics(pd.DataFrame({"pnl": []}), equity_curve=equity)
    first = float(equity.iloc[0])
    year_end = equity.iloc[1:].groupby(equity.index[1:].year).last()
    yearly = year_end / year_end.shift(1).fillna(first) - 1
    trades = result.trades
    return {
        "start": equity.index[0].date().isoformat(),
        "end": equity.index[-1].date().isoformat(),
        "final_equity": _round(float(equity.iloc[-1]), 2),
        "total_return_pct": _round((float(equity.iloc[-1]) / first - 1) * 100),
        "cagr_pct": _round(m["cagr_pct"]),
        "max_drawdown_pct": _round(m["max_drawdown_pct"]),
        "sharpe": _round(m["sharpe_ratio"]),
        "yearly_returns_pct": {int(y): _round(v * 100) for y, v in yearly.items()},
        "positive_years_pct": _round(float((yearly > 0).mean() * 100)),
        "rebalances": int(len(result.turnover)),
        "monthly_turnover_pct": _round(
            float(result.turnover.mean() * 100) if len(result.turnover) else 0.0
        ),
        "avg_holdings": _round(float(result.holdings.mean()), 2),
        "exposure_pct": _round(float((result.holdings > 0).mean() * 100)),
        "avg_invested_pct": _round(float(result.invested.mean() * 100)),
        "trades": int(len(trades)),
        "costs": _round(float(trades["cost"].sum()) if len(trades) else 0.0, 2),
        "delisted_exits": int(result.delisted_exits),
        "no_open_skips": int(result.no_open_skips),
    }


def mar(stats: dict) -> float:
    """Build-period CAGR / |max drawdown|."""
    cagr, dd = stats.get("cagr_pct"), stats.get("max_drawdown_pct")
    if cagr is None or dd is None:
        return -math.inf
    if dd == 0:
        return math.inf if cagr > 0 else 0.0
    return cagr / abs(dd)


def choose_variant(build: dict[str, dict]) -> str:
    """The variant with the highest build-period CAGR/|MaxDD| (ties: name)."""
    return min(build, key=lambda name: (-mar(build[name]), name))
