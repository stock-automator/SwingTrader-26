"""
Earnings dates from SEC EDGAR: 8-K Item 2.02 ("Results of Operations and
Financial Condition") filings, for the earnings-drift backtest (cycle 06).

Sources, all free JSON with no key:

- `www.sec.gov/files/company_tickers.json`: ticker -> CIK, today's tickers
  only;
- `data.sec.gov/submissions/CIK##########.json`: a company's filings, the
  recent ones inline and older ones in paged `files`;
- `efts.sec.gov/LATEST/search-index`: EDGAR full-text search. Press releases
  quote the symbol as "(NYSE: XYZ)" or "(Nasdaq: XYZ)", so searching 8-Ks for
  that string within a removed member's index years finds the company that
  held the symbol then, even when the symbol now belongs to someone else.

SEC asks automated clients for a User-Agent and at most 10 requests a second.
`USER_AGENT` is generic (no name or email), and requests are spaced
`MIN_INTERVAL_SECONDS` apart. Every successful answer is cached as JSON under
the cache directory (`data/edgar/`, gitignored), including empty full-text
results, so a failed lookup is not repeated. HTTP errors raise `EdgarError`
with the status and the start of the body and are never cached.

Day 0 is the filing's `acceptanceDateTime` (UTC) on New York time: at or
after 16:00, or on a day with no session, day 0 is the next session.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

USER_AGENT = "SwingTrader-26 research bot"
#: 8 requests a second, under SEC's limit of 10.
MIN_INTERVAL_SECONDS = 0.125
TIMEOUT_SECONDS = 30
#: Attempts per request on a 5xx answer (full-text search returns transient
#: 500s), `RETRY_WAIT_SECONDS` apart. 4xx answers are never retried.
RETRIES = 3
RETRY_WAIT_SECONDS = 2.0

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"

NEW_YORK = "America/New_York"
CLOSE_HOUR = 16
EARNINGS_ITEM = "2.02"
#: Events are kept from here: one year before the first membership snapshot,
#: so the trailing EAR distribution is full when the backtest starts.
HISTORY_START = "2015-01-01"
#: A CIK whose first 8-K Item 2.02 is later than this after the date it is
#: needed from has a predecessor (a holding-company reorganisation, merger).
PREDECESSOR_GAP_DAYS = 120
#: `pick_cik`: the earnings filer must have at least this many Item 2.02 hits
#: and this share of them.
MIN_HITS = 2
MIN_SHARE = 0.5

#: Membership symbol -> symbol to search for (21st Century Fox traded as
#: FOXA / FOX until its last day, see `pit_prices.QUERY_OVERRIDES`).
SEARCH_SYMBOL: dict[str, str] = {"TFCFA": "FOXA", "TFCF": "FOX"}

#: Removed members full-text search could not resolve (share classes the
#: releases do not quote, symbols last used years ago). Each checked against
#: the EDGAR name / former names and its Item 2.02 dates (2026-11-24).
CIK_OVERRIDES: dict[str, int] = {
    "AA": 4281,  # ALCOA INC. (now Howmet), AA to 2016-10
    "ADS": 1101215,  # ALLIANCE DATA SYSTEMS CORP
    "ANDV": 50104,  # ANDEAVOR / TESORO CORP
    "BBBY": 886158,  # BED BATH & BEYOND INC
    "CCE": 1491675,  # COCA-COLA ENTERPRISES, INC.
    "DISCK": 1437107,  # Discovery Communications, Inc.
    "DO": 949039,  # DIAMOND OFFSHORE DRILLING INC
    "EMC": 790070,  # EMC CORP
    "LLTC": 791907,  # LINEAR TECHNOLOGY CORP
    "PCP": 79958,  # PRECISION CASTPARTS CORP
    "TFCF": 1308161,  # TWENTY-FIRST CENTURY FOX, INC.
    "TWX": 1105705,  # TIME WARNER INC.
    "UAA": 1336917,  # Under Armour, Inc.
    "WFM": 865436,  # WHOLE FOODS MARKET INC
}

#: Current members whose CIK changed while the symbol did not, or whose
#: predecessor traded under another symbol, so full-text search for today's
#: symbol cannot find it. Checked like `CIK_OVERRIDES`.
PREDECESSORS: dict[str, int] = {
    "XOM": 34088,  # EXXON MOBIL CORP, new holding company 2026
    "LIN": 884905,  # LINDE INC (Praxair, PX) before Linde plc
    "VTRS": 1623613,  # Mylan N.V. (MYL) before Viatris
    "SKYD": 813828,  # Paramount Global (PARA) before Paramount Skydance
}

FILING_COLUMNS = ["form", "items", "acceptanceDateTime", "filingDate"]

HttpGet = Callable[..., object]


class EdgarError(RuntimeError):
    """An EDGAR request failed (HTTP status, network, or offline cache miss)."""


def _cik10(cik: int) -> str:
    return f"CIK{int(cik):010d}"


class EdgarClient:
    """Cached, rate-limited EDGAR JSON client. `offline=True` serves the
    cache only and raises on a miss."""

    def __init__(
        self,
        cache_dir: Path,
        http_get: HttpGet = requests.get,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        offline: bool = False,
    ):
        self.cache_dir = Path(cache_dir)
        self._get = http_get
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self.offline = offline
        self.requests = 0

    def _fetch(self, url: str, params: dict | None = None) -> object:
        if self.offline:
            raise EdgarError(f"offline and not cached: {url} {params or ''}")
        for attempt in range(1, RETRIES + 1):
            response = self._request(url, params)
            if response.status_code < 500 or attempt == RETRIES:
                break
            log.warning("%s HTTP %s, retry %d", url, response.status_code, attempt)
            self._sleep(RETRY_WAIT_SECONDS)
        if response.status_code != 200:
            raise EdgarError(
                f"{url} {params or ''} returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        return response.json()

    def _request(self, url: str, params: dict | None):
        if self._last is not None:
            wait = MIN_INTERVAL_SECONDS - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        try:
            return self._get(
                url,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
                timeout=TIMEOUT_SECONDS,
            )
        except Exception as exc:  # network
            raise EdgarError(f"{url}: {type(exc).__name__}: {exc}") from exc
        finally:
            self._last = self._clock()
            self.requests += 1

    def _cached(self, path: Path, url: str, params: dict | None = None) -> object:
        if path.exists():
            return json.loads(path.read_text())
        body = self._fetch(url, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body))
        return body

    def company_tickers(self) -> dict[str, int]:
        """Today's ticker -> CIK, symbols in the membership form (BRK-B)."""
        body = self._cached(self.cache_dir / "company_tickers.json", TICKERS_URL)
        out: dict[str, int] = {}
        for row in body.values():
            out.setdefault(row["ticker"].upper().replace(".", "-"), int(row["cik_str"]))
        return out

    def submission(self, name: str) -> dict:
        path = self.cache_dir / "submissions" / name
        return self._cached(path, SUBMISSIONS_URL.format(name=name))

    def filings(self, cik: int, since: str = HISTORY_START) -> pd.DataFrame:
        """Every filing of `cik` on pages that reach `since` or later."""
        head = self.submission(f"{_cik10(cik)}.json")
        pages = [head["filings"]["recent"]]
        for page in head["filings"].get("files", []):
            if page["filingTo"] >= since:
                pages.append(self.submission(page["name"]))
        frames = [
            pd.DataFrame({k: p.get(k, []) for k in FILING_COLUMNS}) for p in pages
        ]
        return pd.concat(frames, ignore_index=True)

    def fulltext_hits(self, symbol: str, start: str, end: str) -> list[dict]:
        """8-K full-text hits quoting `symbol` as an NYSE or Nasdaq ticker,
        filed from `start` to `end` (first page, up to 100)."""
        params = {
            "q": f'"NYSE: {symbol}" OR "NASDAQ: {symbol}"',
            "forms": "8-K",
            "dateRange": "custom",
            "startdt": start,
            "enddt": end,
        }
        safe = re.sub(r"[^A-Z0-9-]", "_", symbol.upper())
        path = self.cache_dir / "fts" / f"{safe}_{start}_{end}.json"
        body = self._cached(path, FTS_URL, params)
        return body.get("hits", {}).get("hits", [])


def earnings_filings(filings: pd.DataFrame) -> pd.DataFrame:
    """Original 8-Ks (not 8-K/A) listing Item 2.02."""
    items = filings["items"].fillna("").astype(str).str.split(",")
    has_item = items.apply(lambda xs: EARNINGS_ITEM in [x.strip() for x in xs])
    return filings[(filings["form"] == "8-K") & has_item].reset_index(drop=True)


def day_zero(accepted: pd.Series, calendar: pd.DatetimeIndex) -> pd.Series:
    """Session of each acceptance time (UTC ISO strings): the New York date,
    moved to the next session if at or after 16:00 or not a session. NaT
    outside `calendar`."""
    ny = pd.to_datetime(accepted, utc=True).dt.tz_convert(NEW_YORK)
    date = ny.dt.tz_localize(None).dt.normalize()
    target = date + pd.to_timedelta((ny.dt.hour >= CLOSE_HOUR).astype(int), unit="D")
    calendar = pd.DatetimeIndex(calendar)
    pos = calendar.searchsorted(target.to_numpy())
    ok = (pos < len(calendar)) & (target.to_numpy() >= calendar[0])
    out = pd.Series(pd.NaT, index=accepted.index, dtype="datetime64[ns]")
    out[ok] = calendar[pos[ok]]
    return out


def pick_cik(hits: list[dict], exclude: int | None = None) -> int | None:
    """The CIK filing most of the Item 2.02 hits: a company's own earnings
    release quotes its symbol; others (acquirers, peers) are rarer."""
    ciks = [
        int(h["_source"]["ciks"][0])
        for h in hits
        if h["_source"].get("ciks")
        and EARNINGS_ITEM in (h["_source"].get("items") or [])
        and int(h["_source"]["ciks"][0]) != exclude
    ]
    if not ciks:
        return None
    cik, count = Counter(ciks).most_common(1)[0]
    if count < MIN_HITS or count / len(ciks) < MIN_SHARE:
        return None
    return cik


def first_earnings(client, cik: int) -> pd.Timestamp | None:
    found = earnings_filings(client.filings(cik, HISTORY_START))
    if found.empty:
        return None
    return pd.Timestamp(found["filingDate"].min())


def _reported_between(client, cik: int, lo, hi) -> bool:
    found = earnings_filings(client.filings(cik, HISTORY_START))
    days = pd.to_datetime(found["filingDate"])
    return bool(((days >= lo) & (days <= hi)).any())


def resolve_ciks(
    membership: pd.DataFrame,
    client,
    overrides: dict[str, int] | None = None,
    predecessors: dict[str, int] | None = None,
) -> pd.DataFrame:
    """Each member ticker -> its CIK(s): `ticker, cik, source, until`.

    Current members use today's `company_tickers`; removed members (whose
    symbol may now belong to another company) use full-text search over their
    index years. If the CIK first reports more than `PREDECESSOR_GAP_DAYS`
    after it is needed, a predecessor covers the gap (`until` = the
    successor's first Item 2.02): from `predecessors`, else full-text search
    before that date. A removed member search cannot resolve falls back to
    today's owner of the symbol if it filed an Item 2.02 within the member
    span. `overrides` / `predecessors` default to the checked module lists.
    Unresolved tickers get `cik` None."""
    overrides = CIK_OVERRIDES if overrides is None else overrides
    predecessors = PREDECESSORS if predecessors is None else predecessors
    current = client.company_tickers()
    latest, first_snapshot = membership["date"].max(), membership["date"].min()
    span = membership.groupby("ticker")["date"].agg(["min", "max"])
    rows: list[dict] = []
    for ticker, (lo, hi) in span.iterrows():
        symbol = SEARCH_SYMBOL.get(ticker, ticker)
        need_from = pd.Timestamp(HISTORY_START) if lo == first_snapshot else lo
        if ticker in overrides:
            cik, source = overrides[ticker], "override"
        elif hi == latest and ticker in current:
            cik, source = current[ticker], "current"
        else:
            hits = client.fulltext_hits(symbol, _iso(need_from), _iso(hi))
            cik, source = pick_cik(hits), "fulltext"
            if cik is None and ticker in current:
                if _reported_between(client, current[ticker], need_from, hi):
                    cik, source = current[ticker], "current-verified"
        if cik is None:
            rows.append(dict(ticker=ticker, cik=None, source="unresolved", until=None))
            log.warning("no CIK for %s (%s to %s)", ticker, _iso(lo), _iso(hi))
            continue
        rows.append(dict(ticker=ticker, cik=cik, source=source, until=None))
        start = first_earnings(client, cik)
        if start is not None and start <= need_from + pd.Timedelta(
            days=PREDECESSOR_GAP_DAYS
        ):
            continue
        end = start if start is not None else hi
        if ticker in predecessors:
            pred, how = predecessors[ticker], "predecessor-checked"
        else:
            hits = client.fulltext_hits(symbol, _iso(need_from), _iso(end))
            pred, how = pick_cik(hits, exclude=cik), "predecessor"
        if pred is not None:
            rows.append(dict(ticker=ticker, cik=pred, source=how, until=end))
    out = pd.DataFrame(rows, columns=["ticker", "cik", "source", "until"])
    out["until"] = pd.to_datetime(out["until"])
    return out


def _iso(day) -> str:
    return pd.Timestamp(day).date().isoformat()


def build_events(
    resolved: pd.DataFrame, client, calendar: pd.DatetimeIndex
) -> pd.DataFrame:
    """One row per ticker and day 0: `ticker, cik, accepted, day0`. A
    predecessor's filings count only before its `until`."""
    frames = []
    for row in resolved.dropna(subset=["cik"]).itertuples():
        found = earnings_filings(client.filings(int(row.cik), HISTORY_START))
        found = found[found["filingDate"] >= HISTORY_START]
        if pd.notna(row.until):
            found = found[pd.to_datetime(found["filingDate"]) < row.until]
        frames.append(
            pd.DataFrame(
                {
                    "ticker": row.ticker,
                    "cik": int(row.cik),
                    "accepted": found["acceptanceDateTime"].to_numpy(),
                }
            )
        )
    events = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if events.empty:
        return pd.DataFrame(columns=["ticker", "cik", "accepted", "day0"])
    events["day0"] = day_zero(events["accepted"], calendar)
    events = events.dropna(subset=["day0"]).sort_values(["ticker", "accepted"])
    events = events.drop_duplicates(["ticker", "day0"], keep="first")
    return events.reset_index(drop=True)


def member_quarter_coverage(
    events: pd.DataFrame, membership: pd.DataFrame, start, end
) -> dict:
    """% of member-quarters (in the quarter's last snapshot, quarters wholly
    within `start`..`end`) with at least one event (day 0) in the quarter."""
    quarter = membership["date"].dt.to_period("Q")
    last = membership.groupby(quarter)["date"].transform("max")
    m = membership[membership["date"] == last].assign(q=quarter)
    lo, hi = pd.Period(pd.Timestamp(start), "Q"), pd.Period(pd.Timestamp(end), "Q")
    if pd.Timestamp(start) > lo.start_time:
        lo += 1
    if pd.Timestamp(end) < hi.end_time.normalize():
        hi -= 1
    m = m[(m["q"] >= lo) & (m["q"] <= hi)][["ticker", "q"]].drop_duplicates()
    have = events.assign(q=pd.to_datetime(events["day0"]).dt.to_period("Q"))
    have = set(zip(have["ticker"], have["q"]))
    hit = [(t, q) in have for t, q in zip(m["ticker"], m["q"])]
    m = m.assign(hit=hit)
    missing = m.loc[~m["hit"], "ticker"].value_counts()
    total = len(m)
    return {
        "member_quarters": int(total),
        "with_event": int(m["hit"].sum()),
        "pct": float(m["hit"].mean() * 100) if total else 0.0,
        "missing": {str(k): int(v) for k, v in missing.items()},
    }
