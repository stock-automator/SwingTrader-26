#!/usr/bin/env python3
"""
Gap-and-Go day-trade backtest on Alpaca SIP 5-minute bars.

    python scripts/gap_and_go.py download [--start 2016-01-01] [--sample 20]
    python scripts/gap_and_go.py backtest [--start 2016-01-01]

`download` finds candidate gap days in the daily cache (`data/raw`, universe
from `config/universe.json`, FER and spliced-bar tickers excluded), fetches
the regular session of each day for all its gappers in batches (resumable,
< 200 requests/min, retries with backoff, per-request timeout) and writes
`data/intraday/gap_bars/<TICKER>.parquet`. `--sample N` only times N days
and extrapolates. `backtest` prints the build/test tables used in
docs/gap-and-go.md and saves them to `data/reports/gap_and_go.json`.
Keys come from `.env` (ALPACA_API_KEY, ALPACA_SECRET_KEY) and are never
printed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import logging
import os
import random
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.app.data.loader import flatten_columns  # noqa: E402
from backend.app.quant.intraday.downloader import (  # noqa: E402
    RateLimiter,
    consolidate,
    download_dates,
)
from backend.app.quant.intraday.gap_and_go import (  # noqa: E402
    NY,
    GapParams,
    find_gap_candidates,
    simulate_setup,
)
from backend.app.quant.intraday.portfolio import run_portfolio, summarize  # noqa: E402
from backend.app.quant.portfolio import suspect_spike  # noqa: E402

RAW = ROOT / "data" / "raw"
INTRADAY = ROOT / "data" / "intraday"
STAGING = INTRADAY / "_staging"
BARS = INTRADAY / "gap_bars"
REPORT = ROOT / "data" / "reports" / "gap_and_go.json"
EXCLUDE = {"FER"}
SPLIT = pd.Timestamp("2023-01-01")
#: Download down to the robustness check's lowest gap (2% - 20%).
DOWNLOAD_GAP = 0.016
REQUESTS_PER_MIN = 180

log = logging.getLogger("gap_and_go")


def load_daily(ticker: str) -> pd.DataFrame:
    """Split-adjusted (not dividend-adjusted) daily bars, as Alpaca's
    `adjustment=split` 5-minute bars are."""
    df = flatten_columns(pd.read_parquet(RAW / f"{ticker}.parquet"))
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float).dropna()
    return df[~df.index.duplicated(keep="last")].sort_index()


def universe() -> tuple[list[str], list[str]]:
    symbols = json.loads((ROOT / "config" / "universe.json").read_text())["symbols"]
    keep, dropped = [], []
    for s in symbols:
        path = RAW / f"{s}.parquet"
        if s in EXCLUDE or not path.exists() or suspect_spike(load_daily(s)["Close"]):
            dropped.append(s)
        else:
            keep.append(s)
    return keep, dropped


def candidates(start: str, gap_min: float) -> pd.DataFrame:
    tickers, dropped = universe()
    log.info("universe %d tickers, excluded %s", len(tickers), dropped)
    params = GapParams(gap_min=gap_min)
    frames = [find_gap_candidates(load_daily(t), t, params) for t in tickers]
    out = pd.concat(frames, ignore_index=True)
    return out[out["date"] >= pd.Timestamp(start)].reset_index(drop=True)


def alpaca_fetcher(timeout: float = 30.0):
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("ALPACA_API_SECRET")
    if not key or not secret:
        sys.exit("ALPACA_API_KEY / ALPACA_SECRET_KEY missing from the environment")
    client = StockHistoricalDataClient(key, secret)
    # alpaca-py sets no timeout on its requests.Session calls.
    session = client._session
    session.request = functools.partial(session.request, timeout=timeout)

    def fetch(symbols: list[str], day: dt.date) -> pd.DataFrame:
        to_alpaca = {s.replace("-", "."): s for s in symbols}
        req = StockBarsRequest(
            symbol_or_symbols=list(to_alpaca),
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            start=pd.Timestamp(f"{day} 09:30", tz=NY).to_pydatetime(),
            end=pd.Timestamp(f"{day} 16:00", tz=NY).to_pydatetime(),
            feed=DataFeed.SIP,
            adjustment=Adjustment.SPLIT,
        )
        df = client.get_stock_bars(req).df
        if df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        df["symbol"] = df["symbol"].map(lambda s: to_alpaca.get(s, s))
        return df

    return fetch


def cmd_download(args: argparse.Namespace) -> None:
    cands = candidates(args.start, DOWNLOAD_GAP)
    plan = {d.date(): sorted(g["ticker"]) for d, g in cands.groupby("date")}
    log.info("%d ticker-days on %d sessions", len(cands), len(plan))
    fetch = alpaca_fetcher()
    limiter = RateLimiter(REQUESTS_PER_MIN)
    if args.sample:
        days = sorted(random.Random(0).sample(sorted(plan), args.sample))
        sub = {d: plan[d] for d in days}
        t0 = time.monotonic()
        stats = download_dates(fetch, sub, STAGING, limiter=limiter)
        secs = time.monotonic() - t0
        per_req = secs / max(stats["requests"], 1)
        total_req = sum(-(-len(v) // 100) for v in plan.values())
        log.info(
            "sample %s in %.1fs (%.2fs/request); full run ~%d requests, ~%.0f min",
            stats,
            secs,
            per_req,
            total_req,
            total_req * per_req / 60,
        )
        return
    t0 = time.monotonic()
    stats = download_dates(fetch, plan, STAGING, limiter=limiter)
    log.info("download %s in %.1f min", stats, (time.monotonic() - t0) / 60)
    rows = consolidate(STAGING, BARS)
    log.info("wrote %d tickers, %d bars to %s", len(rows), sum(rows.values()), BARS)


def build_setups(cands: pd.DataFrame, params: GapParams) -> pd.DataFrame:
    """Simulate every candidate day that passes `params.gap_min`."""
    cands = cands[cands["gap"].abs() >= params.gap_min - 1e-12]
    rows = []
    for ticker, group in cands.groupby("ticker"):
        path = BARS / f"{ticker}.parquet"
        days = {}
        if path.exists():
            bars = pd.read_parquet(path)
            days = {
                d: g for d, g in bars.groupby(bars.index.tz_localize(None).normalize())
            }
        for c in group.itertuples(index=False):
            day = days.get(c.date)
            base = {
                "date": c.date,
                "ticker": ticker,
                "direction": c.direction,
                "gap": c.gap,
            }
            if day is None:
                rows.append({**base, "status": "no_bars"})
                continue
            res = simulate_setup(
                day, c.direction, c.adv20, c.gap, params, daily_open=c.open
            )
            rows.append({**base, **res})
    return pd.DataFrame(rows)


VARIANTS = {
    "long": ("long",),
    "short": ("short",),
    "both": ("long", "short"),
}


def period_metrics(
    setups: pd.DataFrame,
    sessions: pd.DatetimeIndex,
    spy: pd.Series,
    pdt: bool,
    dirs: tuple[str, ...],
) -> dict:
    s = setups[setups["date"].isin(sessions)]
    res = run_portfolio(s, sessions, pdt=pdt, directions=dirs)
    # Buy-and-hold from the close before the first session.
    prior = spy[spy.index < sessions[0]].iloc[-1:]
    m = summarize(res, pd.concat([prior, spy.reindex(sessions)]))
    m["short_borrow_trades"] = int((res.trades["direction"] == "short").sum())
    return m


def cmd_backtest(args: argparse.Namespace) -> None:
    spy_daily = flatten_columns(pd.read_parquet(RAW / "SPY.parquet"))
    spy = spy_daily["Adj Close"].astype(float).sort_index()
    cands = candidates(args.start, DOWNLOAD_GAP)
    have_bars = {p.stem for p in BARS.glob("*.parquet")}
    last_bar_day = cands.loc[cands["ticker"].isin(have_bars), "date"].max()
    all_sessions = spy.index[
        (spy.index >= pd.Timestamp(args.start)) & (spy.index <= last_bar_day)
    ]
    periods = {
        "build": all_sessions[all_sessions < SPLIT],
        "test": all_sessions[all_sessions >= SPLIT],
    }
    base = GapParams()
    setups = build_setups(cands, base)
    out: dict = {
        "status_counts": setups["status"].value_counts().to_dict(),
        "span": [str(all_sessions[0].date()), str(all_sessions[-1].date())],
        "results": [],
        "robustness": [],
    }
    for period, sessions in periods.items():
        for pdt in (False, True):
            for name, dirs in VARIANTS.items():
                m = period_metrics(setups, sessions, spy, pdt, dirs)
                out["results"].append(
                    {"period": period, "variant": name, "pdt": pdt, **m}
                )
    for gap_min, vol_mult in [(0.016, 2.0), (0.024, 2.0), (0.02, 1.6), (0.02, 2.4)]:
        p = replace(base, gap_min=gap_min, vol_mult=vol_mult)
        s = build_setups(cands, p)
        m = period_metrics(s, periods["build"], spy, False, VARIANTS["both"])
        out["robustness"].append({"gap_min": gap_min, "vol_mult": vol_mult, **m})
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(out, indent=2, default=str))
    print(
        json.dumps(
            {k: out[k] for k in ("span", "status_counts")}, indent=2, default=str
        )
    )
    print(to_markdown(out))


def _fmt(m: dict) -> list[str]:
    def pct(x):
        return "n/a" if pd.isna(x) else f"{x * 100:.1f}%"

    def num(x, d=2):
        return "n/a" if pd.isna(x) else f"{x:.{d}f}"

    return [
        str(m["trades"]),
        pct(m["win_rate"]),
        num(m["payoff"]),
        num(m["profit_factor"]),
        num(m["expectancy_r"], 3),
        pct(m["total_return"]),
        pct(m["cagr"]),
        pct(m["max_drawdown"]),
        num(m["sharpe"]),
        pct(m["pct_positive_years"]),
        pct(m["spy_return"]),
    ]


HEADER = (
    "| Variant | Trades | Win | Payoff | PF | Exp (R) | Total | CAGR | Max DD | Sharpe | Pos. years | SPY B&H |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def to_markdown(out: dict) -> str:
    lines = []
    for period in ("build", "test"):
        lines += [f"\n### {period}\n", HEADER]
        for r in out["results"]:
            if r["period"] == period:
                label = r["variant"] + (" (PDT)" if r["pdt"] else "")
                lines.append("| " + " | ".join([label] + _fmt(r)) + " |")
    lines += [
        "\n### robustness (build, both, no PDT)\n",
        HEADER.replace("Variant", "Gap / vol"),
    ]
    for r in out["robustness"]:
        label = f"{r['gap_min'] * 100:.1f}% / {r['vol_mult']:.1f}x"
        lines.append("| " + " | ".join([label] + _fmt(r)) + " |")
    return "\n".join(lines)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--start", default="2016-01-01")
    d.add_argument("--sample", type=int, default=0)
    b = sub.add_parser("backtest")
    b.add_argument("--start", default="2016-01-01")
    args = ap.parse_args()
    {"download": cmd_download, "backtest": cmd_backtest}[args.cmd](args)


if __name__ == "__main__":
    main()
