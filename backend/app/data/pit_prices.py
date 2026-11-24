"""
Daily bars for point-in-time S&P 500 members that are not in `data/raw`
(cycle 04b). Written to `data/raw_pit/` (gitignored) in the format
`load_prices` reads.

Sources are tried in order (Alpaca SIP daily bars, then yfinance). Alpaca's
`asof` is `ASOF_DAYS` before the ticker's last membership snapshot (never
before its first), so the symbol resolves to the company that held it shortly
before it left: a symbol created by a rename after 2015 (TSO -> ANDV) does not
exist at the first snapshot, and on the event day the symbol can already map
to a successor (AA on 2016-10-31 returns the new Alcoa). Alpaca links a
series by symbol chain, so where a successor kept the symbol the bars after
the event belong to another company: `KEEP` cuts those (found by scanning the
fetched files for one-day moves over 40%), and `QUERY_OVERRIDES` queries a
member by its earlier symbol (21st Century Fox).

Each result, including failures, is kept in `_status.json`: a negative cache,
so failed tickers are not refetched unless asked. Errors are logged as
warnings with secrets scrubbed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

STATUS_FILE = "_status.json"
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

ASOF_DAYS = 35

#: (symbol, asof YYYY-MM-DD) -> OHLCV frame, empty when the source has none.
Fetcher = Callable[[str, str], pd.DataFrame]

#: ticker -> (symbol to query, asof). 21st Century Fox's shares traded as
#: FOXA / FOX until 2019-03-18 and as TFCFA / TFCF on 2019-03-19, its last day.
QUERY_OVERRIDES: dict[str, tuple[str, str]] = {
    "TFCFA": ("FOXA", "2019-01-24"),
    "TFCF": ("FOX", "2019-01-24"),
}

#: ticker -> (first, last) bar to keep, None = open-ended.
KEEP: dict[str, tuple[str | None, str | None]] = {
    # From 2019-03-19 FOXA / FOX are the new Fox Corp.
    "TFCFA": (None, "2019-03-19"),
    "TFCF": (None, "2019-03-19"),
    # AA is the new Alcoa from 2016-11-01 (81.37 -> 20.87); the old one is ARNC.
    "AA": (None, "2016-10-31"),
    # ARNC is the Arconic Corp spin-off from 2020-04-01 (64.24 -> 15.00).
    "ARNC": (None, "2020-03-31"),
    # DWDP became DD with a 1-for-3 reverse split Alpaca left unadjusted.
    "DWDP": (None, "2019-05-31"),
    # Bars before the 2016-05-16 Ingevity spin-off are on another scale
    # (186.22 -> 28.67).
    "WRK": ("2016-05-16", None),
}

#: ticker -> source to try first. Alpaca starts these at a later symbol
#: change (BBWI 2021-08, FTI 2017-01, UAA 2016-04) while yfinance has the
#: history from 2015 under today's symbol.
PREFER: dict[str, str] = {"BBWI": "yfinance", "FTI": "yfinance", "UAA": "yfinance"}


def needed(membership: pd.DataFrame, cache_dirs: Iterable[Path]) -> dict[str, str]:
    """Members with no parquet file in any of `cache_dirs` -> Alpaca asof:
    `ASOF_DAYS` before the last snapshot, but not before the first."""
    cached = {p.stem for d in cache_dirs for p in Path(d).glob("*.parquet")}
    span = membership.groupby("ticker")["date"].agg(["min", "max"]).sort_index()
    asof = (span["max"] - pd.Timedelta(days=ASOF_DAYS)).clip(lower=span["min"])
    return {t: d.date().isoformat() for t, d in asof.items() if t not in cached}


def from_alpaca(df: pd.DataFrame | None) -> pd.DataFrame:
    """Alpaca `get_stock_bars(...).df` -> OHLCV on a naive date index."""
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMNS)
    out = df.reset_index().rename(columns=str.capitalize)
    day = pd.to_datetime(out["Timestamp"], utc=True).dt.tz_convert("America/New_York")
    out.index = pd.DatetimeIndex(day.dt.tz_localize(None).dt.normalize(), name="Date")
    return out[COLUMNS].astype(float)


def write_bars(df: pd.DataFrame, path: Path) -> None:
    out = df[COLUMNS].astype(float).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out["Adj Close"] = out["Close"]  # already adjusted: ratio 1 in the loader
    out.index.name = "Date"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path)


def _scrub(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def fetch_all(
    tickers: dict[str, str],
    out_dir: Path,
    sources: list[tuple[str, Fetcher]],
    retry_failed: bool = False,
    secrets: Iterable[str] = (),
    overrides: dict[str, tuple[str, str]] | None = None,
    keep: dict[str, tuple[str | None, str | None]] | None = None,
    overwrite: bool = False,
    prefer: dict[str, str] | None = None,
) -> dict[str, dict]:
    """Fetch every ticker into `out_dir/<T>.parquet`; returns and saves the
    per-ticker status: `source` (name, "cached" or None) and `errors`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / STATUS_FILE
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    secrets = list(secrets)
    for ticker, asof in tickers.items():
        path = out_dir / f"{ticker}.parquet"
        if path.exists() and not overwrite:
            status[ticker] = {**status.get(ticker, {}), "source": "cached"}
            status[ticker].setdefault("errors", {})
            continue
        if ticker in status and status[ticker].get("source") is None:
            if not (retry_failed or overwrite):
                continue
        symbol, query_asof = (overrides or {}).get(ticker, (ticker, asof))
        first_bar, last_bar = (keep or {}).get(ticker, (None, None))
        errors: dict[str, str] = {}
        got = None
        first = (prefer or {}).get(ticker)
        ordered = sorted(sources, key=lambda source: source[0] != first)
        for name, fetch in ordered:
            try:
                df = fetch(symbol, query_asof)
                if df is not None and (first_bar or last_bar):
                    df = df.loc[first_bar:last_bar]
            except Exception as exc:  # network, auth, symbol unknown
                errors[name] = _scrub(f"{type(exc).__name__}: {exc}", secrets)[:300]
                continue
            if df is not None and not df.empty:
                write_bars(df, path)
                got = name
                break
            errors[name] = "no bars"
        status[ticker] = {"source": got, "asof": asof, "errors": errors}
        if got is None:
            log.warning("no daily bars for %s (asof %s): %s", ticker, asof, errors)
        status_path.write_text(json.dumps(status, indent=1, sort_keys=True))
    status_path.write_text(json.dumps(status, indent=1, sort_keys=True))
    return {t: status[t] for t in tickers if t in status}
