"""
Dynamic trading-universe sync: S&P 500 + Nasdaq-100 constituents, sanitized,
deduped, and filtered down to symbols this repo can actually trade.

`UniverseManager.sync()` is the entry point. It:

1. Fetches both index membership lists (Wikipedia via `pandas.read_html`,
   falling back to a bundled static seed list if the network is unavailable
   or the page shape changes - see `_STATIC_SP500_FALLBACK` /
   `_STATIC_NASDAQ100_FALLBACK`).
2. Sanitizes every ticker onto one notation (see `sanitize_ticker`).
3. Dedupes the merged set.
4. Drops a small denylist of symbols known to have broken/zero-bar data in
   this repo's parquet store (`DENYLIST`).
5. Drops any symbol whose `data/raw/{TICKER}.parquet` is missing or has zero
   rows from the *active* list - see `purge_broken_symbols`, checked across
   a bounded thread pool since this step alone is 500+ per-symbol disk
   reads. This never deletes or edits a parquet file; it only decides what
   goes in `config/universe.json`.
6. Writes the result to `config/universe.json`.

Price data itself (the actual OHLCV bars for each active symbol) is not
fetched here. That pipeline already exists and is reused as-is:
`backend.app.quant.data.parquet_manager.ParquetSyncManager` (wired up via
`POST /api/v1/data/sync`) fetches raw OHLCV + `Adj Close` and reconciles
split/dividend corporate actions retroactively - see that module's
docstring. `backend.app.data.loader.fetch_yfinance`, the other existing
fetch path, already downloads with `auto_adjust=True` (split/dividend
adjusted). `UniverseManager` intentionally does not re-fetch bars for
five-hundred-plus symbols inline: that would make a single `sync()` call
trigger hundreds of synchronous network requests against a rate-limited
provider, duplicating a pipeline that already exists and is already
reachable independently via `/api/v1/data/sync`. Its job is universe
*membership*, not price data.
"""

from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

log = logging.getLogger(__name__)

#: Where `sync()` writes/reads the active universe.
DEFAULT_CONFIG_PATH = Path("config/universe.json")

#: Parquet cache directory this module reads to detect broken/missing data.
#: Matches `backend.app.data.loader.DATA_DIR` / `Settings.data_dir`.
DEFAULT_DATA_DIR = Path("data/raw")

#: How old `synced_at` may be before `needs_sync()` reports true.
SYNC_STALE_AFTER = timedelta(hours=24)

#: Symbols known to have broken/zero-bar data in this repo's parquet store.
#: Excluded from the *active* universe unconditionally - never deleted from
#: disk, and re-included automatically if a future data provider fixes them
#: and they're removed from this set.
DENYLIST: frozenset[str] = frozenset({"BK", "CTRA", "K", "MMC", "WBA"})

#: Realistic desktop-browser User-Agent for constituent-page fetches.
#: Wikipedia (and other index-membership sources) will 403 the default
#: `python-requests/x.y` UA some hosting providers get flagged under -
#: this sidesteps that without doing anything more elaborate than what any
#: real browser sends.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

#: Timeout for constituent-page fetches - short enough that a blocked/absent
#: network (e.g. this repo's own test sandbox) fails fast into the static
#: fallback rather than hanging the caller.
_FETCH_TIMEOUT_SECONDS = 10

#: Wikipedia's "List of S&P 500 companies" - the `Symbol` column of the
#: first table on the page.
_SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

#: Wikipedia's "Nasdaq-100" - the constituents table has a `Ticker` column.
_NASDAQ100_WIKI_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

# ---------------------------------------------------------------------------
# Static fallbacks
#
# Used only when the live Wikipedia fetch fails (no network, page reshaped,
# request blocked, etc.) so `sync()` stays deterministic offline - notably
# in this project's own test suite, which sets `allow_downloads=False` /
# runs in CI sandboxes without network access. Reasonably current as of
# this module's authorship; membership drifts over time (index
# reconstitutions), which is exactly why the live fetch above is preferred
# whenever it succeeds.
# ---------------------------------------------------------------------------

_STATIC_SP500_FALLBACK: tuple[str, ...] = (
    "AAPL",
    "ABBV",
    "ABNB",
    "ABT",
    "ACGL",
    "ACN",
    "ADBE",
    "ADI",
    "ADM",
    "ADP",
    "ADSK",
    "AEE",
    "AEP",
    "AES",
    "AFL",
    "AIG",
    "AIZ",
    "AJG",
    "AKAM",
    "ALB",
    "ALGN",
    "ALL",
    "ALLE",
    "AMAT",
    "AMCR",
    "AMD",
    "AME",
    "AMGN",
    "AMP",
    "AMT",
    "AMZN",
    "ANET",
    "AON",
    "AOS",
    "APA",
    "APD",
    "APH",
    "APO",
    "APTV",
    "ARE",
    "ATO",
    "AVB",
    "AVGO",
    "AVY",
    "AWK",
    "AXON",
    "AXP",
    "AZO",
    "BA",
    "BAC",
    "BALL",
    "BAX",
    "BBY",
    "BDX",
    "BEN",
    "BG",
    "BIIB",
    "BK",
    "BKNG",
    "BKR",
    "BLDR",
    "BLK",
    "BMY",
    "BR",
    "BRO",
    "BSX",
    "BX",
    "BXP",
    "C",
    "CAG",
    "CAH",
    "CARR",
    "CAT",
    "CB",
    "CBOE",
    "CBRE",
    "CCI",
    "CCL",
    "CDNS",
    "CDW",
    "CEG",
    "CF",
    "CFG",
    "CHD",
    "CHRW",
    "CHTR",
    "CI",
    "CINF",
    "CL",
    "CLX",
    "CMCSA",
    "CME",
    "CMG",
    "CMI",
    "CMS",
    "CNC",
    "CNP",
    "COF",
    "COIN",
    "COO",
    "COP",
    "COR",
    "COST",
    "CPAY",
    "CPB",
    "CPRT",
    "CPT",
    "CRL",
    "CRM",
    "CRWD",
    "CSCO",
    "CSGP",
    "CSX",
    "CTAS",
    "CTRA",
    "CTSH",
    "CTVA",
    "CVS",
    "CVX",
    "CZR",
    "D",
    "DAL",
    "DASH",
    "DAY",
    "DD",
    "DE",
    "DECK",
    "DELL",
    "DG",
    "DGX",
    "DHI",
    "DHR",
    "DIS",
    "DLR",
    "DLTR",
    "DOC",
    "DOV",
    "DOW",
    "DPZ",
    "DRI",
    "DTE",
    "DUK",
    "DVA",
    "DVN",
    "DXCM",
    "EA",
    "EBAY",
    "ECL",
    "ED",
    "EFX",
    "EG",
    "EIX",
    "EL",
    "ELV",
    "EMN",
    "EMR",
    "ENPH",
    "EOG",
    "EPAM",
    "EQIX",
    "EQR",
    "EQT",
    "ERIE",
    "ES",
    "ESS",
    "ETN",
    "ETR",
    "EVRG",
    "EW",
    "EXC",
    "EXPD",
    "EXPE",
    "EXR",
    "F",
    "FANG",
    "FAST",
    "FCX",
    "FDS",
    "FDX",
    "FE",
    "FFIV",
    "FI",
    "FICO",
    "FIS",
    "FITB",
    "FOX",
    "FOXA",
    "FRT",
    "FSLR",
    "FTNT",
    "FTV",
    "GD",
    "GDDY",
    "GE",
    "GEHC",
    "GEN",
    "GEV",
    "GILD",
    "GIS",
    "GL",
    "GLW",
    "GM",
    "GNRC",
    "GOOG",
    "GOOGL",
    "GPC",
    "GPN",
    "GRMN",
    "GS",
    "GWW",
    "HAL",
    "HAS",
    "HBAN",
    "HCA",
    "HD",
    "HIG",
    "HII",
    "HLT",
    "HOLX",
    "HON",
    "HPE",
    "HPQ",
    "HRL",
    "HSIC",
    "HST",
    "HSY",
    "HUBB",
    "HUM",
    "HWM",
    "IBM",
    "ICE",
    "IDXX",
    "IEX",
    "IFF",
    "INCY",
    "INTC",
    "INTU",
    "INVH",
    "IP",
    "IPG",
    "IQV",
    "IR",
    "IRM",
    "ISRG",
    "IT",
    "ITW",
    "IVZ",
    "J",
    "JBHT",
    "JBL",
    "JCI",
    "JKHY",
    "JNJ",
    "JPM",
    "K",
    "KDP",
    "KEY",
    "KEYS",
    "KHC",
    "KIM",
    "KKR",
    "KLAC",
    "KMB",
    "KMI",
    "KMX",
    "KO",
    "KR",
    "KVUE",
    "L",
    "LDOS",
    "LEN",
    "LH",
    "LHX",
    "LII",
    "LIN",
    "LKQ",
    "LLY",
    "LMT",
    "LNT",
    "LOW",
    "LRCX",
    "LULU",
    "LUV",
    "LVS",
    "LW",
    "LYB",
    "LYV",
    "MA",
    "MAA",
    "MAR",
    "MAS",
    "MCD",
    "MCHP",
    "MCK",
    "MCO",
    "MDLZ",
    "MDT",
    "MET",
    "META",
    "MGM",
    "MHK",
    "MKC",
    "MKTX",
    "MLM",
    "MMC",
    "MMM",
    "MNST",
    "MO",
    "MOH",
    "MOS",
    "MPC",
    "MPWR",
    "MRK",
    "MRNA",
    "MS",
    "MSCI",
    "MSFT",
    "MSI",
    "MTB",
    "MTCH",
    "MTD",
    "MU",
    "NCLH",
    "NDAQ",
    "NDSN",
    "NEE",
    "NEM",
    "NFLX",
    "NI",
    "NKE",
    "NOC",
    "NOW",
    "NRG",
    "NSC",
    "NTAP",
    "NTRS",
    "NUE",
    "NVDA",
    "NVR",
    "NWS",
    "NWSA",
    "NXPI",
    "O",
    "ODFL",
    "OKE",
    "OMC",
    "ON",
    "ORCL",
    "ORLY",
    "OTIS",
    "OXY",
    "PANW",
    "PAYC",
    "PAYX",
    "PCAR",
    "PCG",
    "PEG",
    "PEP",
    "PFE",
    "PFG",
    "PG",
    "PGR",
    "PH",
    "PHM",
    "PKG",
    "PLD",
    "PLTR",
    "PM",
    "PNC",
    "PNR",
    "PNW",
    "PODD",
    "POOL",
    "PPG",
    "PPL",
    "PRU",
    "PSA",
    "PSX",
    "PTC",
    "PWR",
    "PYPL",
    "QCOM",
    "RCL",
    "REG",
    "REGN",
    "RF",
    "RJF",
    "RL",
    "RMD",
    "ROK",
    "ROL",
    "ROP",
    "ROST",
    "RSG",
    "RTX",
    "RVTY",
    "SBAC",
    "SBUX",
    "SCHW",
    "SHW",
    "SJM",
    "SLB",
    "SMCI",
    "SNA",
    "SNPS",
    "SO",
    "SOLV",
    "SPG",
    "SPGI",
    "SRE",
    "STE",
    "STLD",
    "STT",
    "STX",
    "STZ",
    "SW",
    "SWK",
    "SWKS",
    "SYF",
    "SYK",
    "SYY",
    "T",
    "TAP",
    "TDG",
    "TDY",
    "TECH",
    "TEL",
    "TER",
    "TFC",
    "TGT",
    "TJX",
    "TMO",
    "TMUS",
    "TPL",
    "TPR",
    "TRGP",
    "TRMB",
    "TROW",
    "TRV",
    "TSCO",
    "TSLA",
    "TSN",
    "TT",
    "TTWO",
    "TXN",
    "TXT",
    "TYL",
    "UAL",
    "UBER",
    "UDR",
    "UHS",
    "ULTA",
    "UNH",
    "UNP",
    "UPS",
    "URI",
    "USB",
    "V",
    "VICI",
    "VLO",
    "VLTO",
    "VMC",
    "VRSK",
    "VRSN",
    "VRTX",
    "VST",
    "VTR",
    "VTRS",
    "VZ",
    "WAB",
    "WAT",
    "WBA",
    "WBD",
    "WDC",
    "WEC",
    "WELL",
    "WFC",
    "WM",
    "WMB",
    "WMT",
    "WRB",
    "WSM",
    "WST",
    "WTW",
    "WY",
    "WYNN",
    "XEL",
    "XOM",
    "XYL",
    "YUM",
    "ZBH",
    "ZBRA",
    "ZTS",
)

_STATIC_NASDAQ100_FALLBACK: tuple[str, ...] = (
    "ADBE",
    "ADI",
    "ADP",
    "ADSK",
    "AEP",
    "AMAT",
    "AMD",
    "AMGN",
    "AMZN",
    "ANSS",
    "APP",
    "ARM",
    "ASML",
    "AVGO",
    "AXON",
    "AZN",
    "BIIB",
    "BKNG",
    "BKR",
    "CCEP",
    "CDNS",
    "CDW",
    "CEG",
    "CHTR",
    "CMCSA",
    "COST",
    "CPRT",
    "CRWD",
    "CSCO",
    "CSGP",
    "CSX",
    "CTAS",
    "CTSH",
    "DASH",
    "DDOG",
    "DLTR",
    "DXCM",
    "EA",
    "EXC",
    "FANG",
    "FAST",
    "FTNT",
    "GEHC",
    "GFS",
    "GILD",
    "GOOG",
    "GOOGL",
    "HON",
    "IDXX",
    "ILMN",
    "INTC",
    "INTU",
    "ISRG",
    "KDP",
    "KHC",
    "KLAC",
    "LIN",
    "LRCX",
    "LULU",
    "MAR",
    "MCHP",
    "MDLZ",
    "MELI",
    "META",
    "MNST",
    "MRVL",
    "MSFT",
    "MSTR",
    "MU",
    "NFLX",
    "NVDA",
    "NXPI",
    "ODFL",
    "ON",
    "ORLY",
    "PANW",
    "PAYX",
    "PCAR",
    "PDD",
    "PEP",
    "PLTR",
    "PYPL",
    "QCOM",
    "REGN",
    "ROP",
    "ROST",
    "SBUX",
    "SHOP",
    "SNPS",
    "TEAM",
    "TMUS",
    "TRI",
    "TTD",
    "TTWO",
    "TXN",
    "VRSK",
    "VRTX",
    "WBD",
    "WDAY",
    "XEL",
    "ZS",
)


def sanitize_ticker(raw: str) -> str:
    """Normalize one raw ticker string onto this project's convention.

    Rules:
        - Uppercase, and strip surrounding whitespace.
        - Share-class notation is normalized to the *hyphen* form
          (`BRK.B` -> `BRK-B`), not the dot form. This matches yfinance's
          own ticker symbols, which is what every price fetch in this repo
          (`data.loader.fetch_yfinance`, `quant.data.parquet_manager.
          _default_fetch_raw`) passes straight through to `yfinance` - a
          dotted ticker like `BRK.B` is rejected by yfinance, so the hyphen
          form is the only one that round-trips through the rest of the
          pipeline.

    Wikipedia's S&P 500 table uses dots (`BRK.B`, `BF.B`); Nasdaq-100's
    table already uses no such tickers, but the same rule is applied
    uniformly in case a future addition does.
    """
    return raw.strip().upper().replace(".", "-")


@dataclass(frozen=True)
class UniverseSyncResult:
    """Outcome of one `UniverseManager.sync()` call."""

    symbols: list[str]
    synced_at: str
    source_counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbols": self.symbols,
            "synced_at": self.synced_at,
            "source_counts": self.source_counts,
        }


def _parse_synced_at(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class UniverseManager:
    """Syncs the S&P 500 + Nasdaq-100 membership into `config/universe.json`.

    Args:
        config_path: Where the active universe is persisted.
        data_dir: Parquet cache directory checked by `purge_broken_symbols`.
        denylist: Symbols excluded from the active universe unconditionally.
    """

    def __init__(
        self,
        config_path: Path | str = DEFAULT_CONFIG_PATH,
        data_dir: Path | str = DEFAULT_DATA_DIR,
        denylist: frozenset[str] = DENYLIST,
        max_workers: int = 16,
    ) -> None:
        self.config_path = Path(config_path)
        self.data_dir = Path(data_dir)
        self.denylist = denylist
        #: `purge_broken_symbols` thread-pool size - matches the default of
        #: `Settings.screener_max_workers` (`api.deps.load_frames`'s own
        #: bound) so this module doesn't invent a second concurrency knob.
        self.max_workers = max_workers

    # ------------------------------------------------------------------
    # Constituent fetching
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_tables(url: str) -> list[pd.DataFrame]:
        """`pd.read_html` against `url`, fetched with a browser-like
        User-Agent first rather than handed straight to `read_html`.

        Wikipedia (and similar constituent-list sources) will 403 the bare
        `python-requests/x.y` UA `read_html`'s default urllib backend sends
        from some hosting providers/IP ranges - fetching the page ourselves
        with `_BROWSER_HEADERS` and parsing the resulting HTML text sidesteps
        that.
        """
        response = requests.get(
            url, headers=_BROWSER_HEADERS, timeout=_FETCH_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return pd.read_html(StringIO(response.text))

    def fetch_sp500_constituents(self) -> list[str]:
        """Current S&P 500 tickers, scraped from Wikipedia.

        Falls back to `_STATIC_SP500_FALLBACK` (with a logged warning) on
        any failure - no network, a reshaped page, an unexpected column
        name, etc.
        """
        try:
            tables = self._fetch_tables(_SP500_WIKI_URL)
            symbols_col = tables[0]["Symbol"]
            symbols = [sanitize_ticker(str(s)) for s in symbols_col.tolist()]
            symbols = [s for s in symbols if s]
            if not symbols:
                raise ValueError("Wikipedia S&P 500 table returned no symbols")
            return symbols
        except Exception as exc:  # network, parse, schema-change - all fall back
            log.warning(
                "fetch_sp500_constituents: live fetch failed (%s); "
                "falling back to static seed list of %d symbols",
                exc,
                len(_STATIC_SP500_FALLBACK),
            )
            return list(_STATIC_SP500_FALLBACK)

    def fetch_nasdaq100_constituents(self) -> list[str]:
        """Current Nasdaq-100 tickers, scraped from Wikipedia.

        Falls back to `_STATIC_NASDAQ100_FALLBACK` (with a logged warning)
        on any failure. Wikipedia's Nasdaq-100 page has carried the
        constituents table under either a `Ticker` or `Symbol` header at
        different times, so both are tried before giving up.
        """
        try:
            tables = self._fetch_tables(_NASDAQ100_WIKI_URL)
            symbols: list[str] | None = None
            for table in tables:
                for column in ("Ticker", "Symbol"):
                    if column in table.columns:
                        symbols = [
                            sanitize_ticker(str(s)) for s in table[column].tolist()
                        ]
                        break
                if symbols:
                    break
            if not symbols:
                raise ValueError("Wikipedia Nasdaq-100 table returned no symbols")
            return [s for s in symbols if s]
        except Exception as exc:  # network, parse, schema-change - all fall back
            log.warning(
                "fetch_nasdaq100_constituents: live fetch failed (%s); "
                "falling back to static seed list of %d symbols",
                exc,
                len(_STATIC_NASDAQ100_FALLBACK),
            )
            return list(_STATIC_NASDAQ100_FALLBACK)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _has_usable_data(self, ticker: str) -> bool:
        """Whether `data_dir/{ticker}.parquet` exists and has at least one
        row. Never raises - a corrupt/unreadable file counts as unusable,
        same as a missing one."""
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
        """`symbols` minus anything with a missing or zero-row parquet
        cache file. Purely a filter over `data_dir` - never writes or
        deletes any parquet file.

        Checked across a bounded `ThreadPoolExecutor` (matching
        `quant.regime.MarketRegimeEngine.compute_breadth`'s concurrency
        model) rather than a serial loop - each per-symbol check is a cheap
        parquet-metadata read, but a full S&P 500 + Nasdaq-100 union still
        adds up to 500+ synchronous disk reads serially. Order is preserved
        regardless of completion order.
        """
        if not symbols:
            return []
        workers = max(1, min(self.max_workers, len(symbols)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            usable = list(pool.map(self._has_usable_data, symbols))
        return [s for s, ok in zip(symbols, usable) if ok]

    def _sanitize_and_dedupe(self, symbols: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for raw in symbols:
            ticker = sanitize_ticker(raw)
            if ticker:
                seen[ticker] = None
        return list(seen)

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync(self) -> UniverseSyncResult:
        """Fetch, sanitize, dedupe, and filter the S&P 500 + Nasdaq-100
        union into the active universe, then persist it to `config_path`.

        `source_counts` reports the raw (pre-merge, pre-filter) count from
        each source, so a caller can tell "Wikipedia returned 503 S&P
        names" from "3 got dropped for broken data" independently.
        """
        sp500 = self.fetch_sp500_constituents()
        nasdaq100 = self.fetch_nasdaq100_constituents()

        merged = self._sanitize_and_dedupe(sp500 + nasdaq100)
        merged = [s for s in merged if s not in self.denylist]
        active = sorted(self.purge_broken_symbols(merged))

        result = UniverseSyncResult(
            symbols=active,
            synced_at=datetime.now(timezone.utc).isoformat(),
            source_counts={"sp500": len(sp500), "nasdaq100": len(nasdaq100)},
        )
        self._write(result)
        return result

    def needs_sync(self) -> bool:
        """True if `config_path` doesn't exist, is unreadable, or its
        `synced_at` is more than `SYNC_STALE_AFTER` old."""
        if not self.config_path.exists():
            return True

        try:
            payload = json.loads(self.config_path.read_text())
        except (OSError, json.JSONDecodeError):
            return True

        synced_at = _parse_synced_at(payload.get("synced_at", ""))
        if synced_at is None:
            return True

        return datetime.now(timezone.utc) - synced_at > SYNC_STALE_AFTER

    def read(self) -> UniverseSyncResult | None:
        """The currently persisted universe, or `None` if `sync()` has
        never run (or the file is unreadable/malformed)."""
        if not self.config_path.exists():
            return None
        try:
            payload = json.loads(self.config_path.read_text())
            return UniverseSyncResult(
                symbols=list(payload["symbols"]),
                synced_at=payload["synced_at"],
                source_counts=dict(payload["source_counts"]),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def _write(self, result: UniverseSyncResult) -> None:
        """Atomic write, with a per-call-unique temp filename.

        The startup hook can kick off a background sync at the same moment
        a client hits `POST /sync` - two `UniverseManager` instances then
        race to write the same `config_path`. A shared fixed temp filename
        (e.g. `.with_suffix(".tmp")`) would let one writer's `replace()` be
        beaten to the rename by the other, raising `FileNotFoundError` on
        whichever tmp file already got moved. A unique name per call means
        both writes succeed independently; the later `replace()` just wins,
        which is fine - either result is a valid, freshly-synced universe.
        """
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.config_path.with_name(
            f"{self.config_path.name}.{uuid.uuid4().hex}.tmp"
        )
        tmp_path.write_text(json.dumps(result.as_dict(), indent=2))
        tmp_path.replace(self.config_path)


# ---------------------------------------------------------------------------
# Startup wiring
#
# `backend.app.main` cannot be edited by this change directly (another agent
# owns central router/startup wiring there); `register_universe_startup` is
# the single integration point it needs to call instead. See the module
# docstring of `backend/app/api/universe.py` for the exact snippet.
# ---------------------------------------------------------------------------

#: Module-level singleton so a repeated call (e.g. multiple app instances in
#: tests) doesn't stack duplicate cron jobs on the same process.
_scheduler: Any = None


def _run_sync_soon(manager: UniverseManager) -> None:
    """Kick off `manager.sync()` without blocking the caller.

    Inside a running event loop (the normal FastAPI-startup case), this
    schedules the blocking sync onto a worker thread via `asyncio.to_thread`
    so it never stalls the loop. Outside one (e.g. a plain script or a
    synchronous test calling this directly), it just runs inline.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        manager.sync()
        return

    loop.create_task(asyncio.to_thread(manager.sync))


def check_and_schedule_startup_sync(
    manager: UniverseManager | None = None,
) -> Any:
    """(a) Sync immediately if the universe is missing/stale, and
    (b) (re)schedule the recurring daily sync job.

    Returns the process-wide `AsyncIOScheduler`, started and holding one
    `"universe_daily_sync"` job that fires at 08:30 America/New_York -
    idempotent across repeated calls (`replace_existing=True`, and the
    scheduler itself is a module-level singleton).
    """
    global _scheduler

    manager = manager or UniverseManager()

    if manager.needs_sync():
        _run_sync_soon(manager)

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
        _scheduler.start()

    _scheduler.add_job(
        manager.sync,
        trigger=CronTrigger(hour=8, minute=30, timezone="America/New_York"),
        id="universe_daily_sync",
        replace_existing=True,
    )

    return _scheduler


def register_universe_startup(app: Any) -> None:
    """The only integration point `main.py` needs: registers a FastAPI
    startup handler that runs `check_and_schedule_startup_sync()`.

    Usage (in `main.py`, after constructing `app`):

        from .data.universe import register_universe_startup
        register_universe_startup(app)
    """

    @app.on_event("startup")
    async def _universe_startup() -> (
        None
    ):  # pragma: no cover - exercised via TestClient
        check_and_schedule_startup_sync()
