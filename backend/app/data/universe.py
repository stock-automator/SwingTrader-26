"""
Trading universe: S&P 500 + Nasdaq-100 constituents in `config/universe.json`,
the only ticker list the app scans. Refreshed monthly (scheduler job
`universe_monthly`) or on demand via `POST /api/v1/universe/sync`.

`UniverseManager.sync()`:

1. Fetches both index lists (symbol and company name) from Wikipedia.
2. Keeps the previous file byte-for-byte, logs a warning and records why
   in the status file, if a source fails or returns too few names
   (`MIN_LIVE_COUNTS`).
3. Sanitizes tickers (`BRK.B` -> `BRK-B`), dedupes, and drops the
   `DENY_ENTRIES` symbols.
4. Fetches history (`ParquetSyncManager.sync_ticker`) for names that are
   new versus the previous file, then drops any symbol whose parquet is
   still missing or empty (`purge_broken_symbols`).
5. Diffs against the previous file: added, removed (flagged only; their
   parquet is never touched) and renamed, matched on normalised company
   name (FI -> FISV). Writes symbols, names and `last_changes`.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import requests

from ..quant.data.parquet_manager import ParquetSyncManager

log = logging.getLogger(__name__)

#: Where `sync()` writes/reads the active universe.
DEFAULT_CONFIG_PATH = Path("config/universe.json")

#: Parquet cache directory (matches `Settings.data_dir`).
DEFAULT_DATA_DIR = Path("data/raw")

#: Fewer live names than this means a broken page: keep the previous file.
MIN_LIVE_COUNTS = {"sp500": 450, "nasdaq100": 90}

#: How a denied symbol left the tradable universe.
DenyKind = Literal["rename", "stock_merger", "cash_buyout", "broken_data"]


@dataclass(frozen=True)
class DenyEntry:
    """Why one symbol is excluded from the active universe.

    `since` is the first session after the symbol's last trading day under
    this company (None only for `broken_data`, which has no corporate event).
    `successor` is the symbol holders ended up with (`rename` /
    `stock_merger`); None for cash buyouts and broken data.
    """

    kind: DenyKind
    since: date | None
    reason: str
    successor: str | None = None


#: Symbols excluded from the *active* universe unconditionally, each with
#: the corporate event that removed it. Entries are dated because exchanges
#: reuse tickers: if a symbol comes back for a different company, remove
#: its entry on purpose. Excluding a symbol never deletes it from disk; the
#: pre-delisting history of KVUE and WBD is archived in `data/delisted/`.
DENY_ENTRIES: dict[str, DenyEntry] = {
    "EA": DenyEntry(
        kind="cash_buyout",
        since=date(2026, 8, 4),
        reason="Taken private for cash by PIF, Silver Lake and Affinity Partners.",
    ),
    "KVUE": DenyEntry(
        kind="stock_merger",
        since=date(2026, 11, 2),
        reason="Acquired by Kimberly-Clark for cash plus KMB shares.",
        successor="KMB",
    ),
    "WBD": DenyEntry(
        kind="cash_buyout",
        since=date(2026, 10, 6),
        reason="Acquired by Paramount Skydance for cash.",
    ),
    "AVB": DenyEntry(
        kind="stock_merger",
        since=date(2026, 8, 18),
        reason="Merged into EQR, renamed Vivmark Residential (VMRK).",
        successor="VMRK",
    ),
    "EQR": DenyEntry(
        kind="rename",
        since=date(2026, 8, 18),
        reason="Renamed Vivmark Residential (VMRK) after the AVB merger.",
        successor="VMRK",
    ),
    "BK": DenyEntry(
        kind="rename",
        since=date(2026, 5, 21),
        reason="Bank of New York Mellon now trades as BNY.",
        successor="BNY",
    ),
    "MMC": DenyEntry(
        kind="rename",
        since=date(2026, 1, 14),
        reason="Marsh & McLennan now trades as MRSH.",
        successor="MRSH",
    ),
    "CTRA": DenyEntry(
        kind="stock_merger",
        since=date(2026, 5, 7),
        reason="Merged into Devon Energy (DVN) in an all-stock deal.",
        successor="DVN",
    ),
    "K": DenyEntry(
        kind="cash_buyout",
        since=date(2025, 12, 11),
        reason="Acquired by Mars for cash.",
    ),
    "WBA": DenyEntry(
        kind="cash_buyout",
        since=date(2025, 8, 28),
        reason="Taken private for cash by Sycamore Partners.",
    ),
}

#: Derived set of denied symbols; every existing caller keys off this.
DENYLIST: frozenset[str] = frozenset(DENY_ENTRIES)


#: Realistic desktop-browser User-Agent; Wikipedia may 403 the default
#: `python-requests/x.y` UA.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

#: Timeout for constituent-page fetches, so a blocked network fails fast.
_FETCH_TIMEOUT_SECONDS = 10

#: Wikipedia's "List of S&P 500 companies": `Symbol` and `Security` columns.
_SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

#: Wikipedia's "List of NASDAQ-100 companies": `Ticker` and `Company`
#: columns. The constituents table left the main "Nasdaq-100" article in 2026.
_NASDAQ100_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"

#: source -> (url, symbol columns, company-name columns), first match wins.
_SOURCES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "sp500": (_SP500_WIKI_URL, ("Symbol",), ("Security",)),
    "nasdaq100": (_NASDAQ100_WIKI_URL, ("Ticker", "Symbol"), ("Company", "Security")),
}


def sanitize_ticker(raw: str) -> str:
    """Uppercase, strip, and use the hyphen share-class form yfinance
    expects (`BRK.B` -> `BRK-B`)."""
    return raw.strip().upper().replace(".", "-")


def sanitize_and_dedupe(symbols: list[str]) -> list[str]:
    """`sanitize_ticker` each symbol, drop empties, and dedupe keeping
    first-seen order."""
    seen: dict[str, None] = {}
    for raw in symbols:
        ticker = sanitize_ticker(raw) if raw else ""
        if ticker:
            seen[ticker] = None
    return list(seen)


#: Trailing legal-form words ignored when matching company names.
_NAME_SUFFIXES = frozenset({"inc", "incorporated", "corp", "corporation", "ltd", "plc"})


def normalise_company_name(name: str) -> str:
    """Lowercase, punctuation-free company name without a trailing legal
    form, so "Fiserv, Inc." and "Fiserv Inc" match."""
    words = re.sub(r"[^a-z0-9]+", " ", str(name).lower()).split()
    while words and words[-1] in _NAME_SUFFIXES:
        words.pop()
    return " ".join(words)


def _no_changes() -> dict[str, list]:
    return {"added": [], "removed": [], "renamed": []}


def diff_universe(
    previous: list[str],
    previous_names: dict[str, str],
    current: list[str],
    current_names: dict[str, str],
) -> dict[str, list]:
    """Added, removed and renamed symbols between two universes. A removed
    and an added symbol with the same normalised company name count as one
    rename, not as an add plus a remove."""
    added = sorted(set(current) - set(previous))
    removed = sorted(set(previous) - set(current))
    by_name: dict[str, str] = {}
    for symbol in added:
        key = normalise_company_name(current_names.get(symbol, ""))
        if key:
            by_name.setdefault(key, symbol)
    renamed = []
    for old in removed:
        key = normalise_company_name(previous_names.get(old, ""))
        new = by_name.pop(key, None) if key else None
        if new:
            renamed.append({"old": old, "new": new})
    olds = {r["old"] for r in renamed}
    news = {r["new"] for r in renamed}
    return {
        "added": [s for s in added if s not in news],
        "removed": [s for s in removed if s not in olds],
        "renamed": renamed,
    }


@dataclass(frozen=True)
class UniverseSyncResult:
    """Outcome of one `UniverseManager.sync()` call (or the stored file).

    `source_status` is "live" or "fallback" per source; `kept_previous`
    means the previous file was left untouched and `symbols` are its list.
    """

    symbols: list[str]
    synced_at: str
    source_counts: dict[str, int]
    source_status: dict[str, str] = field(default_factory=dict)
    changes: dict[str, list] = field(default_factory=_no_changes)
    kept_previous: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbols": self.symbols,
            "synced_at": self.synced_at,
            "source_counts": self.source_counts,
            "source_status": self.source_status,
            "changes": self.changes,
            "kept_previous": self.kept_previous,
        }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write via a per-call-unique temp file, so concurrent writers never
    clash and a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
    tmp_path.replace(path)


class UniverseManager:
    """Syncs the S&P 500 + Nasdaq-100 membership into `config_path`.

    Args:
        config_path: Where the active universe is persisted.
        data_dir: Parquet cache directory; history for new names goes here.
        denylist: Symbols excluded from the active universe unconditionally.
        max_workers: Thread-pool size for `purge_broken_symbols`.
        status_path: Optional small JSON file recording every sync attempt
            (including kept-previous failures) for the weekly report.
    """

    def __init__(
        self,
        config_path: Path | str = DEFAULT_CONFIG_PATH,
        data_dir: Path | str = DEFAULT_DATA_DIR,
        denylist: frozenset[str] = DENYLIST,
        max_workers: int = 16,
        status_path: Path | str | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.data_dir = Path(data_dir)
        self.denylist = denylist
        self.max_workers = max_workers
        self.status_path = Path(status_path) if status_path is not None else None

    # ------------------------------------------------------------------
    # Constituent fetching
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_tables(url: str) -> list[pd.DataFrame]:
        """`pd.read_html` of `url`, fetched with a browser User-Agent."""
        response = requests.get(
            url, headers=_BROWSER_HEADERS, timeout=_FETCH_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return pd.read_html(StringIO(response.text))

    def fetch_constituents(self, source: str) -> list[tuple[str, str]] | None:
        """`(symbol, company)` rows for `source` ("sp500" / "nasdaq100"),
        or `None` (logged) if the page can't be fetched or has no table with
        the expected columns."""
        url, symbol_cols, name_cols = _SOURCES[source]
        try:
            for table in self._fetch_tables(url):
                sym = next((c for c in symbol_cols if c in table.columns), None)
                name = next((c for c in name_cols if c in table.columns), None)
                if sym is None or name is None:
                    continue
                rows = [
                    (sanitize_ticker(str(s)), str(n).strip())
                    for s, n in zip(table[sym], table[name])
                ]
                rows = [(s, n) for s, n in rows if s]
                if rows:
                    return rows
            raise ValueError(f"no table with {symbol_cols} and {name_cols}")
        except Exception as exc:  # network, parse, schema change
            log.warning("universe sync: %s live fetch failed (%s)", source, exc)
            return None

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _has_usable_data(self, ticker: str) -> bool:
        """Whether `data_dir/{ticker}.parquet` exists and has a row. A
        corrupt file counts as unusable; never raises."""
        path = self.data_dir / f"{ticker}.parquet"
        if not path.exists():
            return False
        try:
            return len(pd.read_parquet(path, columns=[])) > 0
        except Exception:
            try:
                return len(pd.read_parquet(path)) > 0
            except Exception as exc:
                log.warning("purge_broken_symbols: %s is unreadable (%s)", ticker, exc)
                return False

    def purge_broken_symbols(self, symbols: list[str]) -> list[str]:
        """`symbols` minus anything with a missing or zero-row parquet,
        checked on a bounded thread pool. Never writes or deletes a file."""
        if not symbols:
            return []
        workers = max(1, min(self.max_workers, len(symbols)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            usable = list(pool.map(self._has_usable_data, symbols))
        return [s for s, ok in zip(symbols, usable) if ok]

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync(self) -> UniverseSyncResult:
        """Refresh the universe; see the module docstring for the steps."""
        now = datetime.now(timezone.utc).isoformat()
        previous = self._read_payload() or {}
        fetched = {source: self.fetch_constituents(source) for source in _SOURCES}
        status = {
            s: "live" if rows is not None else "fallback" for s, rows in fetched.items()
        }
        counts = {s: len(rows or ()) for s, rows in fetched.items()}

        reason = self._keep_reason(status, counts)
        if reason:
            log.warning("universe sync: %s; kept the previous universe file", reason)
            result = UniverseSyncResult(
                symbols=list(previous.get("symbols", [])),
                synced_at=str(previous.get("synced_at", "")),
                source_counts=counts,
                source_status=status,
                kept_previous=True,
            )
            self._record_status(now, result, reason)
            return result

        names: dict[str, str] = {}
        for rows in fetched.values():
            for symbol, company in rows or ():
                names.setdefault(symbol, company)
        denied = sorted(set(names) & self.denylist)
        if denied:
            log.warning(
                "universe sync: live lists include denied symbols %s; excluding "
                "them. A reused ticker needs its DENY_ENTRIES row reviewed.",
                ", ".join(denied),
            )
        wanted = sorted(s for s in names if s not in self.denylist)

        previous_symbols = list(previous.get("symbols", []))
        new = sorted(set(wanted) - set(previous_symbols))
        fetch_errors: dict[str, str] = {}
        if new:
            history = ParquetSyncManager(data_dir=self.data_dir)
            for ticker in new:
                try:
                    outcome = history.sync_ticker(ticker)
                except Exception as exc:
                    fetch_errors[ticker] = f"history fetch failed: {exc}"
                    continue
                if getattr(outcome, "status", None) == "error":
                    detail = getattr(outcome, "error", None) or "no data"
                    fetch_errors[ticker] = f"history fetch failed: {detail}"
        active = sorted(self.purge_broken_symbols(wanted))
        active_set = set(active)
        skipped = [
            {
                "symbol": s,
                "reason": fetch_errors.get(s, "missing or unreadable price history"),
            }
            for s in wanted
            if s not in active_set
        ]
        skipped_set = {e["symbol"] for e in skipped}

        # Skipped symbols are still index members: never "removed", and a
        # skipped new member is not "added" either.
        changes = diff_universe(
            previous_symbols,
            dict(previous.get("names") or {}),
            sorted(active_set | skipped_set),
            names,
        )
        changes["added"] = [s for s in changes["added"] if s not in skipped_set]
        changes["renamed"] = [
            r for r in changes["renamed"] if r["new"] not in skipped_set
        ]
        changes["skipped"] = skipped
        result = UniverseSyncResult(
            symbols=active,
            synced_at=now,
            source_counts=counts,
            source_status=status,
            changes=changes,
        )
        self._write(result, {s: names[s] for s in active})
        self._record_status(now, result, None)
        return result

    @staticmethod
    def _keep_reason(status: dict[str, str], counts: dict[str, int]) -> str | None:
        fell_back = [s for s, v in status.items() if v != "live"]
        if fell_back:
            return f"source fell back: {', '.join(fell_back)}"
        short = [
            f"{s} {counts[s]} < {minimum}"
            for s, minimum in MIN_LIVE_COUNTS.items()
            if counts[s] < minimum
        ]
        return f"too few live names: {', '.join(short)}" if short else None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _read_payload(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.config_path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or not isinstance(
            payload.get("symbols"), list
        ):
            return None
        return payload

    def read(self) -> UniverseSyncResult | None:
        """The persisted universe, or `None` if the file is missing or has
        no `symbols` list."""
        payload = self._read_payload()
        if payload is None:
            return None
        return UniverseSyncResult(
            symbols=[str(s) for s in payload["symbols"]],
            synced_at=str(payload.get("synced_at", "")),
            source_counts=dict(payload.get("source_counts") or {}),
            source_status=dict(payload.get("source_status") or {}),
            changes=dict(payload.get("last_changes") or _no_changes()),
        )

    def _write(self, result: UniverseSyncResult, names: dict[str, str]) -> None:
        _atomic_write_json(
            self.config_path,
            {
                "symbols": result.symbols,
                "synced_at": result.synced_at,
                "source_counts": result.source_counts,
                "source_status": result.source_status,
                "names": names,
                "last_changes": result.changes,
            },
        )

    def _record_status(
        self, attempted_at: str, result: UniverseSyncResult, reason: str | None
    ) -> None:
        """Record this attempt in `status_path` (if set) for the weekly
        report. A failure here is logged, never raised."""
        if self.status_path is None:
            return
        try:
            _atomic_write_json(
                self.status_path,
                {
                    "last_attempt": attempted_at,
                    "kept_previous": result.kept_previous,
                    "reason": reason,
                    "source_status": result.source_status,
                    "last_changes": result.changes,
                },
            )
        except OSError as exc:
            log.warning("universe sync: could not write status file (%s)", exc)


#: The process-wide APScheduler (`backend.app.scheduler` creates it).
_scheduler: Any = None
