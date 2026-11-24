"""Point-in-time S&P 500 membership (cycle 04b)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from backend.app.data import sp500_history as h

ROOT = Path(__file__).resolve().parent.parent
MEMBERSHIP = ROOT / "config" / "sp500_membership.csv"


def _changes(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["date", "added", "removed"]).assign(
        date=lambda d: pd.to_datetime(d["date"])
    )


# --- parsing ----------------------------------------------------------------


def test_parse_changes_reads_the_two_level_wikipedia_table():
    cols = pd.MultiIndex.from_tuples(
        [
            ("Effective Date", "Effective Date"),
            ("Added", "Ticker"),
            ("Added", "Security"),
            ("Removed", "Ticker"),
            ("Removed", "Security"),
            ("Reason", "Reason"),
        ]
    )
    raw = pd.DataFrame(
        [
            ["October 30, 2026", "OKTA", "Okta", "KVUE", "Kenvue", "x"],
            ["October 6, 2026", None, None, "CTVA", "Corteva", "x"],
            ["April 8, 2016", "UA.C", "Under Armour", None, None, "x"],
        ],
        columns=cols,
    )
    other = pd.DataFrame({"Symbol": ["MMM"], "Security": ["3M"]})
    out = h.parse_changes([other, raw])
    assert list(out.columns) == ["date", "added", "removed"]
    assert (
        out["date"].tolist()
        == pd.to_datetime(["2026-10-30", "2026-10-06", "2016-04-08"]).tolist()
    )
    assert out["added"].tolist() == ["OKTA", None, "UA-C"]
    assert out["removed"].tolist() == ["KVUE", "CTVA", None]


def test_parse_changes_fails_loudly_without_the_table():
    with pytest.raises(h.SourceError, match="changes"):
        h.parse_changes([pd.DataFrame({"Symbol": ["MMM"]})])


def test_parse_current_reads_symbols_and_fails_loudly_without_them():
    tables = [pd.DataFrame({"x": [1]}), pd.DataFrame({"Symbol": ["BRK.B", " mmm "]})]
    assert h.parse_current(tables) == ["BRK-B", "MMM"]
    with pytest.raises(h.SourceError, match="Symbol"):
        h.parse_current([pd.DataFrame({"x": [1]})])


# --- reconstruction ---------------------------------------------------------


def test_reconstruct_walks_back_from_todays_list():
    changes = _changes(
        [
            ("2020-03-02", "C", "D"),
            ("2020-01-15", "B", "E"),
        ]
    )
    dates = pd.to_datetime(["2019-12-31", "2020-01-31", "2020-02-29", "2020-03-31"])
    members, conflicts = h.reconstruct({"A", "B", "C"}, changes, dates)
    got = members.groupby("date")["ticker"].apply(lambda s: sorted(s)).to_dict()
    assert got == {
        pd.Timestamp("2019-12-31"): ["A", "D", "E"],
        pd.Timestamp("2020-01-31"): ["A", "B", "D"],
        pd.Timestamp("2020-02-29"): ["A", "B", "D"],
        pd.Timestamp("2020-03-31"): ["A", "B", "C"],
    }
    assert conflicts == []


def test_reconstruct_change_on_the_date_counts_as_effective():
    changes = _changes([("2020-01-31", "B", "A")])
    members, _ = h.reconstruct({"B"}, changes, pd.to_datetime(["2020-01-31"]))
    assert members["ticker"].tolist() == ["B"]


def test_reconstruct_maps_renamed_additions_to_todays_symbol():
    # FLT joined in 2018 and is now CPAY: CPAY must leave the set before 2018.
    changes = _changes([("2018-06-20", "FLT", "X")])
    dates = pd.to_datetime(["2018-05-31", "2018-06-29"])
    members, conflicts = h.reconstruct(
        {"CPAY", "A"}, changes, dates, renames={"FLT": "CPAY"}
    )
    got = members.groupby("date")["ticker"].apply(lambda s: sorted(s)).to_dict()
    assert got[pd.Timestamp("2018-05-31")] == ["A", "X"]
    assert got[pd.Timestamp("2018-06-29")] == ["A", "CPAY"]
    assert conflicts == []


def test_reconstruct_reports_conflicts_instead_of_hiding_them():
    changes = _changes([("2020-01-15", "NOPE", None), ("2020-01-10", None, "A")])
    _, conflicts = h.reconstruct({"A"}, changes, pd.to_datetime(["2019-12-31"]))
    assert len(conflicts) == 2
    assert "NOPE" in conflicts[0] and "A" in conflicts[1]


def test_replay_forward_reproduces_todays_list():
    changes = _changes([("2020-03-02", "C", "D"), ("2020-01-15", "B", "E")])
    start = pd.Timestamp("2019-12-31")
    members, _ = h.reconstruct({"A", "B", "C"}, changes, pd.DatetimeIndex([start]))
    first = set(members["ticker"])
    assert h.replay(first, changes, after=start) == {"A", "B", "C"}


def test_month_ends_include_today():
    dates = h.snapshot_dates("2015-12-01", pd.Timestamp("2016-02-10"))
    assert (
        dates.tolist()
        == pd.to_datetime(["2015-12-31", "2016-01-31", "2016-02-10"]).tolist()
    )


def test_build_membership_raises_loudly_when_a_source_is_unreachable(monkeypatch):
    def boom(url):
        raise h.SourceError(f"{url} unreachable")

    monkeypatch.setattr(h, "fetch_tables", boom)
    with pytest.raises(h.SourceError, match="unreachable"):
        h.build_membership("2015-12-01", pd.Timestamp("2026-11-24"))


def test_build_membership_refuses_unresolved_conflicts(monkeypatch):
    current = pd.DataFrame({"Symbol": ["A"]})
    cols = pd.MultiIndex.from_tuples(
        [
            ("Effective Date", "Effective Date"),
            ("Added", "Ticker"),
            ("Removed", "Ticker"),
        ]
    )
    changes = pd.DataFrame([["January 5, 2026", "GHOST", "Z"]], columns=cols)
    pages = {h.SP500_URL: [current], h.HISTORY_URL: [changes]}
    monkeypatch.setattr(h, "fetch_tables", lambda url: pages[url])
    with pytest.raises(h.SourceError, match="GHOST"):
        h.build_membership("2025-12-01", pd.Timestamp("2026-02-10"))


# --- committed file ---------------------------------------------------------


@pytest.fixture(scope="module")
def committed() -> pd.DataFrame:
    df = pd.read_csv(MEMBERSHIP, parse_dates=["date"])
    assert list(df.columns) == ["date", "ticker"]
    return df


def test_committed_membership_is_monthly_from_2015_to_today(committed):
    dates = pd.DatetimeIndex(sorted(committed["date"].unique()))
    assert dates[0] == pd.Timestamp("2015-12-31")
    months = dates.to_period("M")
    assert months.is_unique
    assert (months[1:] - months[:-1]).map(lambda d: d.n).tolist() == [1] * (
        len(months) - 1
    )
    assert dates[-1] >= pd.Timestamp("2026-11-01")


def test_committed_membership_sizes_are_plausible(committed):
    sizes = committed.groupby("date").size()
    assert sizes.between(498, 507).all(), sizes[~sizes.between(498, 507)]
    assert not committed.duplicated().any()


def test_committed_latest_snapshot_matches_todays_list(committed):
    """The walk starts from today's list, so the last snapshot must equal the
    S&P 500 list the universe sync recorded."""
    universe = json.loads((ROOT / "config" / "universe.json").read_text())
    latest = committed[committed["date"] == committed["date"].max()]
    assert len(latest) == universe["source_counts"]["sp500"]
    assert set(latest["ticker"]) <= set(universe["symbols"])


def test_committed_membership_holds_known_removals(committed):
    def members(day: str) -> set[str]:
        return set(committed.loc[committed["date"] == day, "ticker"])

    # Kenvue left on 2026-10-30, Twitter on 2022-11-01, Tesla joined 2020-12-21.
    assert "KVUE" in members("2026-09-30")
    assert "TWTR" in members("2022-09-30") and "TWTR" not in members("2022-11-30")
    assert "TSLA" not in members("2020-11-30") and "TSLA" in members("2020-12-31")
    # Renamed joiners keep today's symbol: FleetCor (FLT, 2018) is CPAY.
    assert "CPAY" in members("2018-06-30") and "CPAY" not in members("2018-05-31")
    assert "FLT" not in set(committed["ticker"])
