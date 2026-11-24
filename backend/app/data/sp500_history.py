"""
Point-in-time S&P 500 membership (cycle 04b).

Starts from today's list ("List of S&P 500 companies") and walks backwards
through the change log, which in 2026 moved from that page to Wikipedia's
"Historical components of the S&P 500". A snapshot is taken at every calendar
month end plus today; membership on a date includes changes effective on it.

Every company keeps its latest symbol: a joiner that was later renamed is
mapped to today's symbol with `RENAMES`, so prices come from one file per
company. A change that does not fit the walk (adding a name that is not in
the later set, removing one that is) is a conflict, and `build_membership`
refuses to return a list with any. Unreachable pages raise `SourceError`.
"""

from __future__ import annotations

import logging
from io import StringIO

import pandas as pd
import requests

from .universe import _BROWSER_HEADERS, sanitize_ticker

log = logging.getLogger(__name__)

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
HISTORY_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
FETCH_TIMEOUT_SECONDS = 20

#: Symbol at joining -> symbol today, for members renamed after joining (the
#: change log records the join under the old symbol and has no rename rows).
RENAMES: dict[str, str] = {
    "SATS": "ECHO",  # EchoStar, joined 2026-03-23
    "CDAY": "DAY",  # Ceridian -> Dayforce, joined 2021-09-20
    "FLT": "CPAY",  # FleetCor -> Corpay, joined 2018-06-20
    "RE": "EG",  # Everest Re -> Everest Group, joined 2017-06-19
    "UA-C": "UA",  # Under Armour class C, joined 2016-04-08
}

#: Changes the log omits. Smurfit Westrock replaced WestRock on 2024-07-08
#: (S&P DJI; the current list's "Date added" for SW agrees).
MISSING_CHANGES: list[tuple[str, str | None, str | None]] = [
    ("2024-07-08", "SW", "WRK"),
]


class SourceError(RuntimeError):
    """A membership source is unreachable or no longer has the expected table."""


def fetch_tables(url: str) -> list[pd.DataFrame]:
    """`pd.read_html` of `url`, fetched with a generic browser User-Agent."""
    try:
        response = requests.get(
            url, headers=_BROWSER_HEADERS, timeout=FETCH_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return pd.read_html(StringIO(response.text))
    except Exception as exc:  # network, HTTP status, no tables
        raise SourceError(f"{url} unreachable or unparsable: {exc}") from exc


def _ticker(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return sanitize_ticker(value)


def parse_current(tables: list[pd.DataFrame]) -> list[str]:
    for table in tables:
        if "Symbol" in table.columns:
            return [t for t in map(_ticker, table["Symbol"]) if t]
    raise SourceError("no table with a Symbol column on the S&P 500 list page")


def parse_changes(tables: list[pd.DataFrame]) -> pd.DataFrame:
    """The change log as `date, added, removed` (None where a side is empty)."""
    for table in tables:
        cols = table.columns
        if not isinstance(cols, pd.MultiIndex):
            continue
        top = cols.get_level_values(0)
        if "Effective Date" not in top or "Added" not in top:
            continue
        flat = {
            "date": table[("Effective Date", "Effective Date")],
            "added": table[("Added", "Ticker")],
            "removed": table[("Removed", "Ticker")],
        }
        out = pd.DataFrame(flat)
        out["date"] = pd.to_datetime(out["date"], format="%B %d, %Y")
        for side in ("added", "removed"):
            out[side] = out[side].map(_ticker).astype(object)
            out[side] = out[side].where(out[side].notna(), None)
        return out.reset_index(drop=True)
    raise SourceError("no S&P 500 changes table (Effective Date / Added / Removed)")


def snapshot_dates(start, today: pd.Timestamp) -> pd.DatetimeIndex:
    """Every calendar month end from `start` up to `today`, plus `today`."""
    ends = pd.date_range(start, today, freq="ME")
    return ends.append(pd.DatetimeIndex([pd.Timestamp(today).normalize()])).unique()


def reconstruct(
    current: set[str],
    changes: pd.DataFrame,
    dates: pd.DatetimeIndex,
    renames: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Membership at each of `dates` (`date, ticker` rows) and a list of
    conflicts, by undoing changes newest first."""
    renames = renames or {}
    members = set(current)
    conflicts: list[str] = []
    ordered = changes.sort_values("date", ascending=False, kind="stable")
    rows = list(ordered[["date", "added", "removed"]].itertuples(index=False))
    k = 0
    snapshots: list[tuple[pd.Timestamp, str]] = []
    for day in sorted(pd.DatetimeIndex(dates), reverse=True):
        while k < len(rows) and rows[k].date > day:
            date, added, removed = rows[k]
            if added:
                name = renames.get(added, added)
                if name in members:
                    members.discard(name)
                else:
                    conflicts.append(f"{date.date()} added {added}: not a member after")
            if removed:
                if removed in members:
                    conflicts.append(f"{date.date()} removed {removed}: still a member")
                members.add(removed)
            k += 1
        snapshots.extend((day, t) for t in sorted(members))
    out = pd.DataFrame(snapshots, columns=["date", "ticker"])
    return out.sort_values(["date", "ticker"]).reset_index(drop=True), conflicts


def replay(
    members: set[str],
    changes: pd.DataFrame,
    after: pd.Timestamp,
    renames: dict[str, str] | None = None,
) -> set[str]:
    """Apply every change effective after `after`, oldest first."""
    renames = renames or {}
    out = set(members)
    later = changes[changes["date"] > after].sort_values("date", kind="stable")
    for added, removed in later[["added", "removed"]].itertuples(index=False):
        if removed:
            out.discard(removed)
        if added:
            out.add(renames.get(added, added))
    return out


def build_membership(start, today: pd.Timestamp) -> pd.DataFrame:
    """Fetch both pages, reconstruct and check. Raises `SourceError` on an
    unreachable page, a missing table, a conflict or a failed replay."""
    current = set(parse_current(fetch_tables(SP500_URL)))
    changes = parse_changes(fetch_tables(HISTORY_URL))
    extra = pd.DataFrame(MISSING_CHANGES, columns=["date", "added", "removed"])
    extra["date"] = pd.to_datetime(extra["date"])
    extra = extra[extra["date"] >= pd.Timestamp(start)]
    if len(extra):
        changes = pd.concat([changes, extra], ignore_index=True)
    changes = changes[changes["date"] <= today]
    dates = snapshot_dates(start, today)
    members, conflicts = reconstruct(current, changes, dates, RENAMES)
    window = changes[changes["date"] > dates[0]]
    if conflicts:
        raise SourceError("membership walk conflicts: " + "; ".join(conflicts))
    first = set(members.loc[members["date"] == dates[0], "ticker"])
    if replay(first, window, dates[0], RENAMES) != current:
        raise SourceError("replaying the changes forward does not give today's list")
    log.info(
        "S&P 500 membership: %d snapshots, %d changes, %d distinct tickers",
        len(dates),
        len(window),
        members["ticker"].nunique(),
    )
    return members
