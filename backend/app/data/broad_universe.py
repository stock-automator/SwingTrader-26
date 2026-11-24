"""
Broad, survivorship-free US stock universe for cycle 07b (small and mid
caps), pre-registered in `docs/research/strategy-evidence-2026-11.md`.

1. Assets: Alpaca's US-equity list, active and inactive. Candidates are the
   NYSE, Nasdaq and AMEX listings plus OTC symbols: a company delisted from
   an exchange keeps its exchange-era bars under its OTC symbol (BBBYQ has
   Bed Bath & Beyond's Nasdaq history), so dropping OTC would drop the
   losers. Obvious non-commons (warrants, rights, units, preferreds,
   depositary shares, ETFs, 5-letter foreign / ADR / warrant / right / unit
   suffixes) are dropped before download; EDGAR classification (below)
   decides the rest.
2. Bars: SIP daily bars, all adjustments, from `START`, many symbols per
   request (`/v2/stocks/bars`, paged by `next_page_token`). Requests are
   spaced `MIN_INTERVAL_SECONDS` apart (under 200 a minute). Each finished
   batch is written to `<dir>/<SYMBOL>.parquet` (the `load_prices` format)
   and recorded in `_status.json`, so a rerun skips it; a failed batch is
   logged with the keys scrubbed and retried next run.
3. Classification (`classify`): a symbol is a common stock if its EDGAR filer
   files 10-K or 10-Q reports and its SIC code is not a fund or commodity
   pool (`EXCLUDED_SIC`). The CIK is today's `company_tickers` owner when
   that company filed while the symbol had bars, else the full-text-search
   earnings filer (`edgar.pick_cik`) over the bar span. Unresolved symbols
   are reported and excluded.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import pandas as pd
import requests

from . import edgar
from .pit_prices import COLUMNS, write_bars

log = logging.getLogger(__name__)

START = "2015-01-01"
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets"
STATUS_FILE = "_status.json"
ASSETS_FILE = "_assets.parquet"
#: 190 requests a minute at most, under Alpaca's 200.
MIN_INTERVAL_SECONDS = 60.0 / 190
TIMEOUT_SECONDS = 60
PAGE_LIMIT = 10_000
BATCH_SIZE = 50

LISTED = ("NYSE", "NASDAQ", "AMEX")
DELISTED_VENUE = "OTC"
NON_COMMON_NAME = re.compile(
    r"\bwarrants?\b|\brights?\b|\bunits?\b|depositary|\badrs?\b|\betf\b|\betn\b"
    r"|preferred (?:stock|shares|securities)",
    re.IGNORECASE,
)
SYMBOL_OK = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
#: Single-letter class suffixes that are not share classes.
BAD_CLASS = {"U", "W", "R"}
#: Fifth-letter suffixes on Nasdaq / OTC symbols: warrant, right, unit,
#: ADR, foreign.
BAD_FIFTH = set("WRUYF")

HttpGet = Callable[..., object]


def file_symbol(symbol: str) -> str:
    """Alpaca symbol -> repo file / ticker form (BRK.B -> BRK-B)."""
    return symbol.replace(".", "-")


def candidates(assets: pd.DataFrame) -> pd.DataFrame:
    """Assets that may be common stocks, input order kept, with `listed`
    (True on NYSE / Nasdaq / AMEX, False for OTC)."""
    symbol = assets["symbol"].astype(str)
    exchange = assets["exchange"].astype(str)
    name = assets["name"].fillna("").astype(str)
    venue_ok = exchange.isin(LISTED + (DELISTED_VENUE,))
    shape_ok = symbol.str.match(SYMBOL_OK)
    suffix = symbol.str.extract(r"\.([A-Z])$")[0]
    class_ok = ~suffix.isin(BAD_CLASS)
    fifth = (symbol.str.len() == 5) & symbol.str[-1].isin(BAD_FIFTH)
    fifth_ok = ~(fifth & exchange.isin(["NASDAQ", DELISTED_VENUE]))
    name_ok = ~name.str.contains(NON_COMMON_NAME)
    keep = venue_ok & shape_ok & class_ok & fifth_ok & name_ok
    out = assets[keep].copy()
    out["listed"] = exchange[keep].isin(LISTED)
    return out.reset_index(drop=True)


def _scrub(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


class AlpacaError(RuntimeError):
    """An Alpaca request failed (status and body, keys scrubbed)."""


class BarsDownloader:
    """Rate-limited Alpaca REST client for assets and multi-symbol bars."""

    def __init__(
        self,
        key: str,
        secret: str,
        http_get: HttpGet = requests.get,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        batch_size: int = BATCH_SIZE,
        start: str = START,
    ):
        self._headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self._secrets = [key, secret]
        self._get = http_get
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self.batch_size = batch_size
        self.start = start
        self.requests = 0

    def _request(self, url: str, params: dict) -> dict:
        if self._last is not None:
            wait = MIN_INTERVAL_SECONDS - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        try:
            response = self._get(
                url, params=params, headers=self._headers, timeout=TIMEOUT_SECONDS
            )
        except Exception as exc:  # network
            raise AlpacaError(
                _scrub(f"{url}: {type(exc).__name__}: {exc}", self._secrets)
            ) from exc
        finally:
            self._last = self._clock()
            self.requests += 1
        if response.status_code != 200:
            text = _scrub(response.text[:300], self._secrets)
            raise AlpacaError(f"{url} returned HTTP {response.status_code}: {text}")
        return response.json()

    def assets(self) -> pd.DataFrame:
        """US-equity assets, active and inactive."""
        rows: list[dict] = []
        for status in ("active", "inactive"):
            body = self._request(
                ASSETS_URL, {"status": status, "asset_class": "us_equity"}
            )
            rows.extend(body)
        return pd.DataFrame(rows)

    def fetch_batch(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        """Every page of daily bars for `symbols` -> OHLCV per symbol (empty
        frame when none), on a naive New York date index."""
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": self.start,
            "adjustment": "all",
            "feed": "sip",
            "limit": PAGE_LIMIT,
        }
        found: dict[str, list[dict]] = {s: [] for s in symbols}
        token = None
        while True:
            page = dict(params, page_token=token) if token else params
            body = self._request(BARS_URL, page)
            for symbol, bars in (body.get("bars") or {}).items():
                found.setdefault(symbol, []).extend(bars)
            token = body.get("next_page_token")
            if not token:
                break
        return {s: _frame(bars) for s, bars in found.items()}

    def download(self, symbols: list[str], out_dir: Path) -> dict[str, dict]:
        """Fetch every symbol not yet in `_status.json`, a batch at a time;
        returns the status (`bars`, `first`, `last` per symbol)."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / STATUS_FILE
        status = json.loads(path.read_text()) if path.exists() else {}
        todo = [s for s in symbols if s not in status]
        for k in range(0, len(todo), self.batch_size):
            batch = todo[k : k + self.batch_size]  # noqa: E203
            try:
                bars = self.fetch_batch(batch)
            except AlpacaError as exc:
                log.warning("batch %s..%s failed: %s", batch[0], batch[-1], exc)
                continue
            for symbol in batch:
                df = bars.get(symbol, pd.DataFrame(columns=COLUMNS))
                entry = {"bars": int(len(df)), "first": None, "last": None}
                if len(df):
                    write_bars(df, out_dir / f"{file_symbol(symbol)}.parquet")
                    entry["first"] = df.index.min().date().isoformat()
                    entry["last"] = df.index.max().date().isoformat()
                status[symbol] = entry
            path.write_text(json.dumps(status, indent=0, sort_keys=True))
            log.info("%d / %d symbols done", len(status), len(symbols))
        return status


def _frame(bars: list[dict]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=COLUMNS, dtype=float)
    df = pd.DataFrame(bars)
    day = pd.to_datetime(df["t"], utc=True).dt.tz_convert(edgar.NEW_YORK)
    out = pd.DataFrame(
        {
            "Open": df["o"],
            "High": df["h"],
            "Low": df["l"],
            "Close": df["c"],
            "Volume": df["v"],
        }
    ).astype(float)
    out.index = pd.DatetimeIndex(day.dt.tz_localize(None).dt.normalize(), name="Date")
    return out


# --- EDGAR classification ------------------------------------------------------

REPORT_FORMS = {"10-K", "10-Q", "10-K405", "10-KT", "10-QT"}
#: Commodity pools (6221), open-end funds (6722), unit investment trusts and
#: closed-end funds (6726): funds, not operating companies.
EXCLUDED_SIC = {"6221", "6722", "6726"}
#: Today's owner of a symbol counts if it filed within this many days of the
#: symbol's bar span.
SPAN_SLACK_DAYS = 120
CLASSIFY_COLUMNS = ["ticker", "cik", "source", "sic", "common", "reason"]


def _filed_within(filings: pd.DataFrame, first, last) -> bool:
    days = pd.to_datetime(filings["filingDate"])
    slack = pd.Timedelta(days=SPAN_SLACK_DAYS)
    return bool(((days >= first - slack) & (days <= last + slack)).any())


def _search_symbols(ticker: str) -> list[str]:
    """`ticker`, then the pre-bankruptcy symbol of a Q-suffixed one."""
    out = [ticker]
    if len(ticker) >= 4 and ticker.endswith("Q"):
        out.append(ticker[:-1])
    return out


def classify(spans: pd.DataFrame, client) -> pd.DataFrame:
    """`spans` (`ticker, first, last` bar dates, repo symbol form) -> one row
    per ticker: `cik` (None if unresolved), `source` (current / fulltext /
    unresolved), `sic`, `common` and the `reason` when not common."""
    current = client.company_tickers()
    rows: list[dict] = []
    for t in spans.itertuples(index=False):
        try:
            rows.append(_classify_one(t, current, client))
        except edgar.EdgarError as exc:
            log.warning("EDGAR error classifying %s: %s", t.ticker, exc)
            rows.append(
                dict(ticker=t.ticker, cik=None, source="unresolved", sic=None)
                | dict(common=False, reason="EDGAR error")
            )
    return pd.DataFrame(rows, columns=CLASSIFY_COLUMNS)


def _classify_one(t, current: dict[str, int], client) -> dict:
    cik, source = None, "unresolved"
    if t.ticker in current:
        owner = current[t.ticker]
        if _filed_within(client.filings(owner, START), t.first, t.last):
            cik, source = owner, "current"
    if cik is None:
        for symbol in _search_symbols(t.ticker):
            hits = client.fulltext_hits(symbol, edgar._iso(t.first), edgar._iso(t.last))
            found = edgar.pick_cik(hits)
            if found is not None:
                cik, source = found, "fulltext"
                break
    if cik is None:
        return dict(ticker=t.ticker, cik=None, source=source, sic=None) | dict(
            common=False, reason="no CIK"
        )
    sic = str(client.submission(f"CIK{int(cik):010d}.json").get("sic") or "")
    forms = set(client.filings(cik, START)["form"])
    if not forms & REPORT_FORMS:
        common, reason = False, "no 10-K/10-Q"
    elif sic in EXCLUDED_SIC:
        common, reason = False, f"fund SIC {sic}"
    else:
        common, reason = True, ""
    return dict(ticker=t.ticker, cik=int(cik), source=source, sic=sic) | dict(
        common=common, reason=reason
    )


def classification_summary(classified: pd.DataFrame) -> dict:
    resolved = int(classified["cik"].notna().sum())
    return {
        "symbols": int(len(classified)),
        "resolved": resolved,
        "unresolved": int(len(classified) - resolved),
        "common": int(classified["common"].astype(bool).sum()),
    }
