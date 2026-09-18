#!/usr/bin/env python3
"""
Monthly S&P 500 / Nasdaq-100 watchlist sync.

Fetches current index constituents from Wikipedia, merges and deduplicates
them, drops illiquid names (trailing ~20-trading-day average volume under
`MIN_AVG_VOLUME` shares), and rewrites the watchlist file the application
reads from - preserving any leading `#`-comment header already there, and
writing atomically so a crash mid-run can't corrupt the live file.

Path note: the original ask targeted `data/watchlist.txt`, but nothing in
this codebase reads that path - `Settings.watchlist_path`
(`backend/app/config.py`) defaults to `config/watchlist.txt`, and that's
what `api/deps.py`'s `load_watchlist` actually loads for the live screener.
This script targets `config/watchlist.txt` by default (override with
`--output`) so a monthly sync doesn't silently produce a second, unused
file nothing ever reads.

Usage:
    python scripts/update_watchlist.py               # fetch, filter, write
    python scripts/update_watchlist.py --dry-run      # report the diff only
    python scripts/update_watchlist.py --output PATH  # write somewhere else
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd

log = logging.getLogger("update_watchlist")

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
DEFAULT_OUTPUT = Path("config/watchlist.txt")

#: Illiquid-name cutoff, in trailing average daily shares.
MIN_AVG_VOLUME = 1_000_000

#: Trailing trading-day window the average volume is measured over - long
#: enough that one quiet/halted day next to an earnings print doesn't drop
#: an otherwise-liquid ticker.
VOLUME_LOOKBACK_DAYS = 20

#: Column header aliases Wikipedia's tables have used for the ticker column
#: over the years - matched case-insensitively.
TICKER_COLUMN_NAMES = {"symbol", "ticker"}


def _find_ticker_column(
    tables: list[pd.DataFrame], source: str
) -> tuple[pd.DataFrame, str]:
    """The first table (and its ticker-like column name) among `tables`
    that has one - Wikipedia's table shape/order has changed before, so
    this looks for the column rather than trusting a fixed table index.

    Raises:
        ValueError: if no table has a Symbol/Ticker column.
    """
    for table in tables:
        for col in table.columns:
            if str(col).strip().lower() in TICKER_COLUMN_NAMES:
                return table, col
    raise ValueError(f"{source}: no table with a Symbol/Ticker column found")


def _normalize_ticker(raw: str) -> str:
    """Uppercase, trimmed, and dot-vs-dash normalized (Wikipedia spells
    e.g. Berkshire Hathaway as `BRK.B`; yfinance/most US data providers use
    `BRK-B`)."""
    return str(raw).strip().upper().replace(".", "-")


def fetch_index_constituents(url: str, source: str) -> list[str]:
    """Ticker symbols from the Wikipedia page at `url`.

    Raises:
        ValueError: if no table on the page has a recognizable ticker column.
        Exception: whatever `pandas.read_html` raises on a fetch failure -
            propagated as-is so the caller can distinguish "page fetch
            failed" from "page fetched but shape changed unrecognizably".
    """
    tables = pd.read_html(url)
    table, column = _find_ticker_column(tables, source)
    return [_normalize_ticker(t) for t in table[column].dropna()]


def fetch_sp500() -> list[str]:
    return fetch_index_constituents(SP500_URL, "S&P 500")


def fetch_nasdaq100() -> list[str]:
    return fetch_index_constituents(NASDAQ100_URL, "Nasdaq-100")


def merge_and_dedupe(*ticker_lists: list[str]) -> list[str]:
    """Case-insensitive union (inputs are expected already-uppercased),
    sorted alphabetically."""
    merged: set[str] = set()
    for tickers in ticker_lists:
        merged.update(t for t in tickers if t)
    return sorted(merged)


def filter_illiquid(
    tickers: list[str],
    min_avg_volume: float = MIN_AVG_VOLUME,
    lookback_days: int = VOLUME_LOOKBACK_DAYS,
) -> tuple[list[str], list[str]]:
    """Split `tickers` into `(kept, dropped)` by trailing average volume.

    Fetches volume via one batched `yfinance.download` call rather than a
    per-ticker loop - far cheaper and avoids per-ticker rate limiting for a
    several-hundred-name universe. A ticker yfinance can't return data for
    is dropped (with a warning logged), not treated as a crash.
    """
    import yfinance as yf

    if not tickers:
        return [], []

    data = yf.download(
        tickers, period="1mo", progress=False, group_by="ticker", threads=True
    )

    kept: list[str] = []
    dropped: list[str] = []
    single = len(tickers) == 1

    for ticker in tickers:
        try:
            volume = data["Volume"] if single else data[ticker]["Volume"]
            avg_volume = volume.tail(lookback_days).mean()
        except (KeyError, TypeError):
            avg_volume = float("nan")

        if pd.isna(avg_volume):
            log.warning("%s: no volume data returned, dropping", ticker)
            dropped.append(ticker)
        elif avg_volume < min_avg_volume:
            dropped.append(ticker)
        else:
            kept.append(ticker)

    return kept, dropped


def read_existing(path: Path) -> tuple[list[str], list[str]]:
    """`(header_lines, tickers)` from `path` - `header_lines` is whatever
    leading blank/`#`-prefixed lines the file starts with, preserved
    verbatim; `tickers` is every other non-blank line, uppercased. Both are
    empty if `path` doesn't exist yet."""
    if not path.exists():
        return [], []

    lines = path.read_text().splitlines()
    header: list[str] = []
    i = 0
    while i < len(lines) and (
        lines[i].strip() == "" or lines[i].strip().startswith("#")
    ):
        header.append(lines[i])
        i += 1
    tickers = [line.strip().upper() for line in lines[i:] if line.strip()]
    return header, tickers


def write_atomic(path: Path, header: list[str], tickers: list[str]) -> None:
    """Write `header` + `tickers` to `path`, via a same-directory temp file
    + `os.replace` - a crash mid-write leaves the original file intact
    rather than a half-written watchlist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [*header, *tickers]
    content = "\n".join(lines) + ("\n" if lines else "")

    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(content)
    os.replace(tmp_path, path)


def sync(
    output: Path = DEFAULT_OUTPUT,
    dry_run: bool = False,
    min_avg_volume: float = MIN_AVG_VOLUME,
) -> int:
    """Run one full sync. Returns a process exit code: `0` on success
    (including "nothing changed" and "dry run"), `1` only on a genuine
    failure - both index sources failing to fetch."""
    sp500: list[str] = []
    nasdaq100: list[str] = []
    fetch_errors: list[str] = []

    # Broad `except Exception` deliberately: `pandas.read_html` can raise
    # anything from a network error to an lxml parse failure, and a fetch
    # failure on one index source should not abort the run if the other
    # source is still usable.
    try:
        sp500 = fetch_sp500()
    except Exception as exc:
        fetch_errors.append(f"S&P 500: {exc}")
        log.warning("S&P 500 fetch failed: %s", exc)

    try:
        nasdaq100 = fetch_nasdaq100()
    except Exception as exc:
        fetch_errors.append(f"Nasdaq-100: {exc}")
        log.warning("Nasdaq-100 fetch failed: %s", exc)

    if not sp500 and not nasdaq100:
        log.error(
            "Both index sources failed to fetch - aborting. %s",
            "; ".join(fetch_errors),
        )
        return 1

    merged = merge_and_dedupe(sp500, nasdaq100)
    kept, dropped = filter_illiquid(merged, min_avg_volume)

    header, previous_tickers = read_existing(output)
    previous_set = set(previous_tickers)
    new_set = set(kept)
    added = sorted(new_set - previous_set)
    removed = sorted(previous_set - new_set)

    log.info("S&P 500: %d tickers", len(sp500))
    log.info("Nasdaq-100: %d tickers", len(nasdaq100))
    log.info("Merged + deduped: %d tickers", len(merged))
    log.info(
        "Dropped for illiquidity (< %.0f avg volume): %d", min_avg_volume, len(dropped)
    )
    log.info("Final watchlist: %d tickers", len(kept))
    log.info("Added: %s", ", ".join(added) if added else "(none)")
    log.info("Removed: %s", ", ".join(removed) if removed else "(none)")

    if dry_run:
        log.info("Dry run - not writing to %s", output)
        return 0

    if not added and not removed:
        log.info("No changes vs. %s - nothing to write", output)
        return 0

    write_atomic(output, header, kept)
    log.info("Wrote %d tickers to %s", len(kept), output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Watchlist file to update (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing anything.",
    )
    parser.add_argument(
        "--min-avg-volume",
        type=float,
        default=MIN_AVG_VOLUME,
        help=f"Illiquid-name cutoff, trailing avg daily volume (default: {MIN_AVG_VOLUME:.0f})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return sync(args.output, args.dry_run, args.min_avg_volume)


if __name__ == "__main__":
    sys.exit(main())
