#!/usr/bin/env python3
"""
Fetch daily bars for point-in-time S&P 500 members missing from data/raw into
data/raw_pit (gitignored): Alpaca SIP daily bars first (split and dividend
adjusted, `asof` a month before the member left), then yfinance. Failures are
negative-cached in data/raw_pit/_status.json; `--retry-failed` retries them
and `--overwrite` refetches everything.

    python scripts/fetch_pit_prices.py
"""

from __future__ import annotations

import argparse
import functools
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.app.data import pit_prices as pp  # noqa: E402

START = "2015-01-01"
TIMEOUT = 30.0
log = logging.getLogger("fetch_pit_prices")


def alpaca_fetcher() -> tuple[pp.Fetcher, list[str]]:
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("ALPACA_API_SECRET")
    if not key or not secret:
        raise SystemExit("ALPACA_API_KEY / ALPACA_SECRET_KEY missing from .env")
    client = StockHistoricalDataClient(key, secret)
    session = client._session  # alpaca-py sets no timeout of its own
    session.request = functools.partial(session.request, timeout=TIMEOUT)

    def fetch(ticker: str, asof: str) -> pd.DataFrame:
        time.sleep(0.35)  # stay under 200 requests a minute
        req = StockBarsRequest(
            symbol_or_symbols=ticker.replace("-", "."),
            timeframe=TimeFrame.Day,
            start=pd.Timestamp(START).to_pydatetime(),
            feed=DataFeed.SIP,
            adjustment=Adjustment.ALL,
            asof=asof,
        )
        return pp.from_alpaca(client.get_stock_bars(req).df)

    return fetch, [key, secret]


def yfinance_fetch(ticker: str, asof: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(
        ticker, start=START, auto_adjust=True, progress=False, threads=False
    )
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(-1)
    df = df.dropna(subset=["Close"])
    # yfinance serves today's holder of a symbol: reject a history that does
    # not reach back to the membership snapshot (a reused symbol).
    if df.empty or df.index[0] > pd.Timestamp(asof):
        return pd.DataFrame()
    return df[pp.COLUMNS]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--membership", type=Path, default=ROOT / "config/sp500_membership.csv"
    )
    parser.add_argument("--raw", type=Path, default=ROOT / "data/raw")
    parser.add_argument("--out", type=Path, default=ROOT / "data/raw_pit")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--overwrite", action="store_true", help="refetch tickers already saved"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    membership = pd.read_csv(args.membership, parse_dates=["date"])
    todo = pp.needed(membership, [args.raw])
    log.info("%d members are not in %s", len(todo), args.raw)
    alpaca, secrets = alpaca_fetcher()
    status = pp.fetch_all(
        todo,
        args.out,
        sources=[("alpaca", alpaca), ("yfinance", yfinance_fetch)],
        retry_failed=args.retry_failed,
        secrets=secrets,
        overrides=pp.QUERY_OVERRIDES,
        keep=pp.KEEP,
        overwrite=args.overwrite,
        prefer=pp.PREFER,
    )
    by_source = pd.Series([s["source"] for s in status.values()]).value_counts(
        dropna=False
    )
    print(by_source.to_string())
    failed = sorted(t for t, s in status.items() if s["source"] is None)
    if failed:
        print(f"WARNING: no bars for {len(failed)}: {' '.join(failed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
