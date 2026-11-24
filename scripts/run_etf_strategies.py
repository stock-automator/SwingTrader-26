#!/usr/bin/env python3
"""
Cycle 06 backtests 1 (SPY intraday momentum) and 3 (sector-ETF momentum).

    python scripts/run_etf_strategies.py [--refresh]

Fetches Alpaca SIP bars (all-adjusted, i.e. split and dividend) once and
caches them in gitignored folders: SPY 30-minute bars per year plus the
trading calendar in `data/intraday/spy/`, sector-ETF and SPY daily bars in
`data/raw_etf/`. Past years are read from cache; the current year and the
calendar are refetched each run, always ending at midnight New York so no
unfinished session is cached. `--refresh` refetches the daily ETF bars.
Prints the build/test tables used in docs/etf-strategies.md and saves them
to `data/reports/etf_strategies.json`. Keys come from `.env`
(ALPACA_API_KEY, ALPACA_SECRET_KEY) and are never printed.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.app.quant.etf_strategies import (  # noqa: E402
    NY,
    TRADING_DAYS,
    abs_momentum_weights,
    break_even_cost,
    cached_frame,
    distribution_adjust,
    equal_weights,
    intraday_positions,
    last_complete_end,
    month_end_dates,
    perf_metrics,
    run_rebalanced,
    session_legs,
    strategy_returns,
    top_n_weights,
)

SPY_DIR = ROOT / "data" / "intraday" / "spy"
ETF_DIR = ROOT / "data" / "raw_etf"
REPORT = ROOT / "data" / "reports" / "etf_strategies.json"
START = pd.Timestamp("2016-01-01", tz=NY)
SPLIT = pd.Timestamp("2023-01-01")
SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
SECTORS += ["XLRE", "XLC"]
INTRADAY_COSTS = (0.0001, 0.0005)
SECTOR_COST = 0.0002
#: Distributions Alpaca leaves unadjusted: XLF paid out XLRE shares.
UNADJUSTED = {"XLF": pd.Timestamp("2016-09-19")}


def alpaca(timeout: float = 30.0):
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetCalendarRequest
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("ALPACA_API_SECRET")
    if not key or not secret:
        sys.exit("ALPACA_API_KEY / ALPACA_SECRET_KEY missing from the environment")
    data = StockHistoricalDataClient(key, secret)
    trading = TradingClient(key, secret, paper=True)
    for client in (data, trading):
        # alpaca-py sets no timeout on its requests.Session calls.
        client._session.request = functools.partial(
            client._session.request, timeout=timeout
        )

    def bars(symbol: str, minutes: int | None, start, end) -> pd.DataFrame:
        tf = (
            TimeFrame.Day
            if minutes is None
            else TimeFrame(minutes, TimeFrameUnit.Minute)
        )
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start.to_pydatetime(),
            end=end.to_pydatetime(),
            feed=DataFeed.SIP,
            adjustment=Adjustment.ALL,
        )
        df = data.get_stock_bars(req).df
        if df.empty:
            return df
        df = df.xs(symbol, level="symbol")[["open", "high", "low", "close", "volume"]]
        df.index = df.index.tz_convert(NY)
        if minutes is None:
            df.index = df.index.normalize().tz_localize(None)
        return df

    def cal(start, end) -> pd.DataFrame:
        req = GetCalendarRequest(start=start.date(), end=end.date())
        rows = trading.get_calendar(req)
        out = pd.DataFrame(
            {"close": [pd.Timestamp(r.close) for r in rows]},
            index=pd.DatetimeIndex([pd.Timestamp(r.date) for r in rows]),
        )
        return out[out.index < end.tz_localize(None)]

    return bars, cal


def load_data(refresh: bool) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    bars, cal = alpaca()
    end = last_complete_end(pd.Timestamp.now(tz=NY))
    frames = []
    for year in range(START.year, end.year + 1):
        lo = pd.Timestamp(f"{year}-01-01", tz=NY)
        hi = min(pd.Timestamp(f"{year + 1}-01-01", tz=NY), end)
        frames.append(
            cached_frame(
                SPY_DIR / f"spy_30min_{year}.parquet",
                functools.partial(bars, "SPY", 30, lo, hi),
                refresh=year == end.year,
            )
        )
    spy30 = pd.concat(frames).sort_index()
    calendar = cached_frame(
        SPY_DIR / "calendar.parquet",
        functools.partial(cal, START, end),
        refresh=True,
    )
    daily = {
        s: cached_frame(
            ETF_DIR / f"{s}.parquet",
            functools.partial(bars, s, None, START, end),
            refresh=refresh,
        )
        for s in SECTORS + ["SPY"]
    }
    for sym, ex_date in UNADJUSTED.items():
        daily[sym] = distribution_adjust(daily[sym], ex_date)
    return spy30, calendar, daily


def periods(index: pd.DatetimeIndex, first: pd.Timestamp) -> dict:
    return {
        "build": index[(index >= first) & (index < SPLIT)],
        "test": index[index >= SPLIT],
    }


# --------------------------------------------------------------------------
# 1. SPY intraday momentum
# --------------------------------------------------------------------------

VARIANTS = {
    "a every day": dict(pdt=False, long_only=False),
    "b PDT-capped": dict(pdt=True, long_only=False),
    "c long-only every day": dict(pdt=False, long_only=True),
    "c long-only PDT-capped": dict(pdt=True, long_only=True),
}


def intraday(spy30: pd.DataFrame, calendar: pd.DataFrame) -> dict:
    legs = session_legs(spy30, calendar)
    # All rows start once the PDT threshold has 252 previous sessions.
    first = legs.index[TRADING_DAYS]
    spy_bh = legs["last_close"] / legs["prev_close"] - 1
    out: dict = {
        "span": [str(legs.index[0].date()), str(legs.index[-1].date())],
        "sessions": int(len(legs)),
        "early_close_sessions": int(legs["early_close"].sum()),
        "rows": [],
    }
    positions = {
        k: intraday_positions(legs["signal"], **v) for k, v in VARIANTS.items()
    }
    for period, days in periods(legs.index, first).items():
        r_last = legs["r_last"].loc[days]
        drift_bp = float(r_last.mean() * 1e4)
        for name, pos_all in positions.items():
            pos = pos_all.loc[days]
            on = pos != 0
            gross = (pos * r_last)[on]
            for cost in INTRADAY_COSTS:
                daily = strategy_returns(pos, r_last, cost)
                m = perf_metrics(daily, daily[on])
                m.update(
                    period=period,
                    row=f"{name} @{cost * 1e4:.0f}bp",
                    gross_bp=float(gross.mean() * 1e4) if on.any() else None,
                    break_even_bp=break_even_cost(gross) * 1e4 if on.any() else None,
                    em_long_bp=float(r_last[on].mean() * 1e4) if on.any() else None,
                    drift_bp=drift_bp,
                )
                out["rows"].append(m)
        bh = perf_metrics(spy_bh.loc[days])
        bh.update(period=period, row="SPY buy-and-hold", drift_bp=drift_bp)
        always = perf_metrics(r_last, r_last)
        always.update(
            period=period,
            row="SPY long 15:30-16:00 daily (gross)",
            gross_bp=drift_bp,
            drift_bp=drift_bp,
        )
        out["rows"] += [bh, always]
    return out


# --------------------------------------------------------------------------
# 3. Sector-ETF momentum
# --------------------------------------------------------------------------


def sectors(daily: dict) -> dict:
    closes = pd.DataFrame({s: daily[s]["close"] for s in SECTORS}).sort_index()
    opens = pd.DataFrame({s: daily[s]["open"] for s in SECTORS}).reindex(closes.index)
    dates = month_end_dates(closes.index)
    weights = {
        "Rule A top-3 6m": top_n_weights(closes, dates),
        "Rule B 12m > 0": abs_momentum_weights(closes, dates),
        "Equal-weight sectors (monthly)": equal_weights(closes, dates),
    }
    # All rows start at the first execution once a sector has 12 months.
    ready = closes.shift(TRADING_DAYS).loc[dates].notna().any(axis=1)
    first = closes.index[closes.index.get_loc(ready[ready].index[0]) + 1]
    spy = daily["SPY"]["close"].reindex(closes.index).pct_change()
    out: dict = {
        "span": [str(closes.index[0].date()), str(closes.index[-1].date())],
        "first_bar": {s: str(daily[s].index[0].date()) for s in SECTORS},
        "rows": [],
    }
    returns = {
        k: run_rebalanced(opens, closes, w, SECTOR_COST) for k, w in weights.items()
    }
    for period, days in periods(closes.index, first).items():
        for name, r in returns.items():
            w = weights[name]
            executed = closes.index[closes.index.get_indexer(w.index) + 1]
            held = (w[executed.isin(days)] > 0).sum(axis=1)
            m = perf_metrics(r.loc[days])
            m.update(
                period=period,
                row=name,
                avg_held=float(held.mean()),
                pct_months_cash=float((held == 0).mean()),
            )
            out["rows"].append(m)
        m = perf_metrics(spy.loc[days])
        m.update(period=period, row="SPY buy-and-hold")
        out["rows"].append(m)
    return out


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _num(x, d: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{d}f}"


def markdown(intra: dict, sect: dict) -> str:
    lines = []
    for period in ("build", "test"):
        rows = [r for r in intra["rows"] if r["period"] == period]
        lines += [
            f"\n### SPY intraday momentum, {period} ({rows[0]['start']} to {rows[0]['end']})\n",
            "| Row | Trades | Hit | Avg bp net | Gross bp | Break-even bp/side | "
            "SPY long same days bp | Total | CAGR | Max DD | Sharpe | Pos. years |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        r["row"],
                        str(r["trades"]) if r["trades"] is not None else "-",
                        _pct(r["hit_rate"]),
                        _num(r["avg_bp"], 1),
                        _num(r.get("gross_bp"), 1),
                        _num(r.get("break_even_bp"), 2),
                        _num(r.get("em_long_bp"), 1),
                        _pct(r["total_return"]),
                        _pct(r["cagr"]),
                        _pct(r["max_drawdown"]),
                        _num(r["sharpe"]),
                        _pct(r["pct_positive_years"]),
                    ]
                )
                + " |"
            )
        lines.append(
            f"\nSPY average 15:30-16:00 drift: {rows[0]['drift_bp']:.2f} bp per session."
        )
    for period in ("build", "test"):
        rows = [r for r in sect["rows"] if r["period"] == period]
        lines += [
            f"\n### Sector-ETF momentum, {period} ({rows[0]['start']} to {rows[0]['end']})\n",
            "| Row | Total | CAGR | Max DD | Sharpe | Pos. years | Avg held | Months in cash |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        r["row"],
                        _pct(r["total_return"]),
                        _pct(r["cagr"]),
                        _pct(r["max_drawdown"]),
                        _num(r["sharpe"]),
                        _pct(r["pct_positive_years"]),
                        _num(r.get("avg_held"), 1),
                        _pct(r.get("pct_months_cash")),
                    ]
                )
                + " |"
            )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true", help="refetch daily ETF bars")
    args = ap.parse_args()
    spy30, calendar, daily = load_data(args.refresh)
    intra = intraday(spy30, calendar)
    sect = sectors(daily)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"intraday": intra, "sectors": sect}, indent=2))
    meta = {k: intra[k] for k in ("span", "sessions", "early_close_sessions")}
    print(json.dumps({"intraday": meta, "sectors": sect["first_bar"]}, indent=2))
    print(markdown(intra, sect))


if __name__ == "__main__":
    main()
