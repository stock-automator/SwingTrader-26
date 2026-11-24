"""Resumable, rate-limited download of regular-session 5-minute bars.

`download_dates` fetches one session at a time for every symbol gapping that
day, in chunks of `chunk` symbols per request, and stages the result as
`<staging>/<YYYY-MM-DD>.parquet`. A staged file marks the day done, so a
rerun skips it; a day whose request still fails after retries is left
unstaged and retried next run. `consolidate` then writes one
`<TICKER>.parquet` per symbol.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

import pandas as pd

from .gap_and_go import regular_session

log = logging.getLogger(__name__)

T = TypeVar("T")
#: fetch(symbols, day) -> long frame: symbol, timestamp (UTC), open ... volume.
Fetcher = Callable[[list[str], dt.date], pd.DataFrame]
OHLCV = ["Open", "High", "Low", "Close", "Volume"]


class RateLimiter:
    """Spaces calls at least 60 / `per_minute` seconds apart."""

    def __init__(
        self,
        per_minute: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval = 60.0 / per_minute
        self.clock = clock
        self.sleep = sleep
        self._next: float | None = None

    def wait(self) -> None:
        now = self.clock()
        if self._next is not None and now < self._next:
            self.sleep(self._next - now)
            now = self._next
        self._next = now + self.interval


def with_retry(
    fn: Callable[[], T],
    attempts: int = 5,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `fn`, retrying with exponential backoff; re-raise the last error."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # network errors vary by library
            if i == attempts - 1:
                raise
            delay = base_delay * 2**i
            log.warning(
                "request failed (%s), retry in %.0fs", type(exc).__name__, delay
            )
            sleep(delay)
    raise RuntimeError("unreachable")


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        end = i + size
        yield items[i:end]


def download_dates(
    fetch: Fetcher,
    plan: dict[dt.date, list[str]],
    staging: Path,
    chunk: int = 100,
    limiter: RateLimiter | None = None,
    attempts: int = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    staging.mkdir(parents=True, exist_ok=True)
    stats = {"fetched": 0, "skipped": 0, "failed": 0, "requests": 0}
    for day in sorted(plan):
        path = staging / f"{day.isoformat()}.parquet"
        if path.exists():
            stats["skipped"] += 1
            continue
        frames = []
        try:
            for syms in _chunks(sorted(set(plan[day])), chunk):
                if limiter is not None:
                    limiter.wait()
                stats["requests"] += 1
                frames.append(
                    with_retry(lambda: fetch(syms, day), attempts=attempts, sleep=sleep)
                )
        except Exception as exc:
            log.error("%s: giving up this run (%s)", day, type(exc).__name__)
            stats["failed"] += 1
            continue
        raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        staged = _to_session(raw)
        tmp = path.with_suffix(".tmp")
        staged.to_parquet(tmp)
        tmp.rename(path)
        stats["fetched"] += 1
    return stats


def _to_session(raw: pd.DataFrame) -> pd.DataFrame:
    cols = ["symbol", "timestamp"] + OHLCV
    if raw.empty:
        return pd.DataFrame(columns=cols)
    df = raw.rename(columns={c.lower(): c for c in OHLCV})
    df = df.set_index(pd.DatetimeIndex(pd.to_datetime(df["timestamp"], utc=True)))
    df = regular_session(df[["symbol"] + OHLCV])
    return df.rename_axis("timestamp").reset_index()[cols]


def consolidate(staging: Path, out_dir: Path) -> dict[str, int]:
    """Write `<out_dir>/<TICKER>.parquet` from every staged day; rows per ticker."""
    files = sorted(staging.glob("*.parquet"))
    staged = [pd.read_parquet(f) for f in files]
    staged = [s for s in staged if not s.empty]
    out_dir.mkdir(parents=True, exist_ok=True)
    if not staged:
        return {}
    all_bars = pd.concat(staged, ignore_index=True)
    all_bars["timestamp"] = pd.to_datetime(
        all_bars["timestamp"], utc=True
    ).dt.tz_convert("America/New_York")
    rows: dict[str, int] = {}
    for sym, g in all_bars.groupby("symbol"):
        bars = g.set_index("timestamp")[OHLCV].sort_index()
        bars = bars[~bars.index.duplicated(keep="last")]
        bars.to_parquet(out_dir / f"{sym}.parquet")
        rows[str(sym)] = len(bars)
    return rows
