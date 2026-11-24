"""Earnings-announcement-return drift (cycle 06, backtest 2): EDGAR 8-K Item
2.02 dates, day 0 on New York time, EAR deciles and the 60-day hold."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.app.data import edgar

ROOT = Path(__file__).resolve().parent.parent

# --- EDGAR ------------------------------------------------------------------


def _filings(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["form", "items", "acceptanceDateTime"])


def test_earnings_filings_keeps_only_8k_item_202():
    df = _filings(
        [
            ("8-K", "2.02,9.01", "2020-01-28T21:30:40.000Z"),
            ("8-K", "12.02", "2020-02-01T21:30:40.000Z"),
            ("8-K/A", "2.02", "2020-03-01T21:30:40.000Z"),
            ("8-K", "5.02,9.01", "2020-04-01T21:30:40.000Z"),
            ("10-Q", "", "2020-05-01T21:30:40.000Z"),
            ("8-K", "2.02", "2020-06-01T12:00:00.000Z"),
        ]
    )
    out = edgar.earnings_filings(df)
    assert out["acceptanceDateTime"].tolist() == [
        "2020-01-28T21:30:40.000Z",
        "2020-06-01T12:00:00.000Z",
    ]


CAL = pd.bdate_range("2020-06-01", "2020-07-31").drop(pd.Timestamp("2020-07-03"))


@pytest.mark.parametrize(
    "accepted, expected",
    [
        # 15:59 New York (EDT, UTC-4) is before the close: same day.
        ("2020-06-02T19:59:00.000Z", "2020-06-02"),
        # 16:00 New York is at the close: next session.
        ("2020-06-02T20:00:00.000Z", "2020-06-03"),
        # Pre-market: same day.
        ("2020-06-02T11:00:00.000Z", "2020-06-02"),
        # 21:00 UTC on Friday is 17:00 New York: next session is Monday.
        ("2020-06-05T21:00:00.000Z", "2020-06-08"),
        # Saturday morning: Monday.
        ("2020-06-06T14:00:00.000Z", "2020-06-08"),
        # 2020-07-03 is a holiday: Monday 07-06, even before 16:00.
        ("2020-07-03T13:00:00.000Z", "2020-07-06"),
        # 01:00 UTC on 06-03 is 21:00 New York on 06-02: next session 06-03.
        ("2020-06-03T01:00:00.000Z", "2020-06-03"),
    ],
)
def test_day_zero_on_new_york_time(accepted, expected):
    out = edgar.day_zero(pd.Series([accepted]), CAL)
    assert out.iloc[0] == pd.Timestamp(expected)


def test_day_zero_uses_winter_offset():
    cal = pd.bdate_range("2020-01-01", "2020-01-31")
    # 20:30 UTC in January is 15:30 EST (UTC-5): same day.
    out = edgar.day_zero(pd.Series(["2020-01-28T20:30:00.000Z"]), cal)
    assert out.iloc[0] == pd.Timestamp("2020-01-28")
    out = edgar.day_zero(pd.Series(["2020-01-28T21:00:00.000Z"]), cal)
    assert out.iloc[0] == pd.Timestamp("2020-01-29")


def test_day_zero_after_calendar_end_is_nat():
    out = edgar.day_zero(pd.Series(["2020-07-31T21:00:00.000Z"]), CAL)
    assert pd.isna(out.iloc[0])


def _hit(cik: str, items: list[str]) -> dict:
    return {"_source": {"ciks": [cik], "items": items}}


def test_pick_cik_prefers_the_earnings_filer():
    hits = [_hit("0000000002", ["8.01"])] * 6 + [_hit("0000000001", ["2.02"])] * 3
    hits += [_hit("0000000003", ["2.02"])]
    assert edgar.pick_cik(hits) == 1


def test_pick_cik_none_without_a_clear_mode():
    assert edgar.pick_cik([]) is None
    hits = [_hit("0000000001", ["2.02"]), _hit("0000000002", ["2.02"])]
    assert edgar.pick_cik(hits) is None


class FakeHttp:
    """Records requests; answers from a dict of url -> (status, json)."""

    def __init__(self, answers: dict[str, tuple[int, object]]):
        self.answers = answers
        self.calls: list[tuple[str, dict | None, dict]] = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        status, body = self.answers[url]

        class Response:
            status_code = status
            text = json.dumps(body)

            def json(self):
                return body

        return Response()


def _client(tmp_path, answers, **kw) -> tuple[edgar.EdgarClient, FakeHttp]:
    http = FakeHttp(answers)
    return edgar.EdgarClient(tmp_path, http_get=http, sleep=lambda s: None, **kw), http


SUB_URL = "https://data.sec.gov/submissions/CIK0000000001.json"
PAGE_OLD = "https://data.sec.gov/submissions/CIK0000000001-submissions-002.json"
PAGE_NEW = "https://data.sec.gov/submissions/CIK0000000001-submissions-001.json"


def _columns(rows):
    keys = ["form", "items", "acceptanceDateTime", "filingDate"]
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


SUBMISSION = {
    "name": "ONE CORP",
    "filings": {
        "recent": _columns([("8-K", "2.02", "2021-01-28T21:30:00.000Z", "2021-01-28")]),
        "files": [
            {"name": "CIK0000000001-submissions-001.json", "filingTo": "2016-03-01"},
            {"name": "CIK0000000001-submissions-002.json", "filingTo": "2009-12-31"},
        ],
    },
}
PAGE = _columns([("8-K", "2.02", "2015-04-28T12:00:00.000Z", "2015-04-28")])


def test_submissions_merges_pages_since_and_caches(tmp_path):
    answers = {SUB_URL: (200, SUBMISSION), PAGE_NEW: (200, PAGE)}
    client, http = _client(tmp_path, answers)
    df = client.filings(1, since="2015-01-01")
    assert sorted(df["filingDate"]) == ["2015-04-28", "2021-01-28"]
    # The page ending 2009 is never requested.
    assert [c[0] for c in http.calls] == [SUB_URL, PAGE_NEW]
    # Second call is served from data on disk.
    client2, http2 = _client(tmp_path, {})
    assert len(client2.filings(1, since="2015-01-01")) == 2
    assert http2.calls == []


def test_client_sends_generic_user_agent_without_contact_details(tmp_path):
    client, http = _client(
        tmp_path, {SUB_URL: (200, SUBMISSION), PAGE_NEW: (200, PAGE)}
    )
    client.filings(1, since="2015-01-01")
    agent = http.calls[0][2]["User-Agent"]
    assert agent == edgar.USER_AGENT
    assert "@" not in agent


def test_client_raises_with_status_and_does_not_cache_errors(tmp_path):
    client, _ = _client(tmp_path, {SUB_URL: (403, {"message": "Request rejected"})})
    with pytest.raises(edgar.EdgarError, match="403.*Request rejected"):
        client.filings(1, since="2015-01-01")
    assert not list(tmp_path.rglob("*.json"))


class FlakyHttp(FakeHttp):
    """Answers 500 `fails` times, then from `answers`."""

    def __init__(self, answers, fails):
        super().__init__(answers)
        self.fails = fails

    def __call__(self, url, params=None, headers=None, timeout=None):
        if self.fails:
            self.fails -= 1
            self.calls.append((url, params, headers))
            return type("R", (), {"status_code": 500, "text": "Internal"})()
        return super().__call__(url, params, headers, timeout)


def test_client_retries_server_errors_then_gives_up(tmp_path):
    answers = {SUB_URL: (200, SUBMISSION), PAGE_NEW: (200, PAGE)}
    http = FlakyHttp(answers, fails=edgar.RETRIES - 1)
    client = edgar.EdgarClient(tmp_path / "a", http_get=http, sleep=lambda s: None)
    assert len(client.filings(1, since="2015-01-01")) == 2
    http = FlakyHttp(answers, fails=edgar.RETRIES)
    client = edgar.EdgarClient(tmp_path / "b", http_get=http, sleep=lambda s: None)
    with pytest.raises(edgar.EdgarError, match="500"):
        client.filings(1, since="2015-01-01")
    assert len(http.calls) == edgar.RETRIES


def test_client_offline_raises_on_cache_miss(tmp_path):
    client, http = _client(tmp_path, {}, offline=True)
    with pytest.raises(edgar.EdgarError, match="offline"):
        client.filings(1, since="2015-01-01")
    assert http.calls == []


def test_client_stays_under_rate_limit(tmp_path):
    clock = {"t": 0.0}
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        clock["t"] += s

    http = FakeHttp({SUB_URL: (200, SUBMISSION), PAGE_NEW: (200, PAGE)})
    client = edgar.EdgarClient(
        tmp_path, http_get=http, sleep=sleep, clock=lambda: clock["t"]
    )
    client.filings(1, since="2015-01-01")
    assert edgar.MIN_INTERVAL_SECONDS >= 0.1
    assert sum(slept) == pytest.approx(edgar.MIN_INTERVAL_SECONDS)


def test_fulltext_hits_query_and_negative_cache(tmp_path):
    url = edgar.FTS_URL
    client, http = _client(tmp_path, {url: (200, {"hits": {"hits": []}})})
    assert client.fulltext_hits("XYZ", "2016-01-01", "2017-01-01") == []
    params = http.calls[0][1]
    assert '"NYSE: XYZ"' in params["q"] and '"NASDAQ: XYZ"' in params["q"]
    assert params["forms"] == "8-K"
    # An empty answer is cached too, so a failed lookup is not repeated.
    client2, http2 = _client(tmp_path, {})
    assert client2.fulltext_hits("XYZ", "2016-01-01", "2017-01-01") == []
    assert http2.calls == []


def test_resolve_current_ticker_from_company_tickers():
    membership = pd.DataFrame(
        {"date": pd.to_datetime(["2016-01-31", "2026-01-31"]), "ticker": ["AAA"] * 2}
    )

    class Client:
        def company_tickers(self):
            return {"AAA": 11}

        def fulltext_hits(self, *a):
            raise AssertionError("not needed")

        def filings(self, cik, since):
            return pd.DataFrame(
                {
                    "form": ["8-K"],
                    "items": ["2.02"],
                    "acceptanceDateTime": ["2015-02-01T12:00:00.000Z"],
                    "filingDate": ["2015-02-01"],
                }
            )

    out = edgar.resolve_ciks(membership, Client())
    assert out.loc[0, ["ticker", "cik", "source"]].tolist() == ["AAA", 11, "current"]


def test_resolve_removed_ticker_by_fulltext_and_predecessor():
    membership = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2015-12-31", "2019-06-30", "2015-12-31", "2026-01-31"]
            ),
            "ticker": ["OLD", "OLD", "NEW", "NEW"],
        }
    )
    first_filing = {21: "2015-01-20", 31: "2019-04-01", 32: "2015-01-10"}

    class Client:
        def company_tickers(self):
            return {"OLD": 99, "NEW": 31}  # OLD's symbol is reused today

        def fulltext_hits(self, ticker, start, end):
            if ticker == "OLD":
                return [_hit("0000000021", ["2.02"])] * 3
            assert end <= "2019-04-01"
            return [_hit("0000000032", ["2.02"])] * 3

        def filings(self, cik, since):
            return pd.DataFrame(
                {
                    "form": ["8-K"],
                    "items": ["2.02"],
                    "acceptanceDateTime": [first_filing[cik] + "T12:00:00.000Z"],
                    "filingDate": [first_filing[cik]],
                }
            )

    out = edgar.resolve_ciks(membership, Client()).set_index(["ticker", "cik"])
    assert out.loc[("OLD", 21), "source"] == "fulltext"
    assert out.loc[("NEW", 31), "source"] == "current"
    # NEW's current CIK starts filing in 2019: the predecessor covers before.
    assert out.loc[("NEW", 32), "source"] == "predecessor"
    assert out.loc[("NEW", 32), "until"] == pd.Timestamp("2019-04-01")


def test_member_quarter_coverage():
    membership = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2020-03-31", "2020-03-31", "2020-06-30", "2020-06-30"]
            ),
            "ticker": ["A", "B", "A", "B"],
        }
    )
    events = pd.DataFrame(
        {
            "ticker": ["A", "A", "B"],
            "day0": pd.to_datetime(["2020-02-01", "2020-05-01", "2020-02-02"]),
        }
    )
    cov = edgar.member_quarter_coverage(events, membership, "2020-01-01", "2020-06-30")
    assert cov["member_quarters"] == 4
    assert cov["with_event"] == 3
    assert cov["pct"] == pytest.approx(75.0)
    assert cov["missing"] == {"B": 1}


EVENTS_CACHE = ROOT / "data" / "edgar" / "events.parquet"


@pytest.mark.skipif(not EVENTS_CACHE.exists(), reason="no EDGAR events cache")
def test_real_events_cache_coverage():
    """Data step check: the cached EDGAR events cover at least 95% of S&P 500
    member-quarters 2016 to 2025, and none lands on a non-session day."""
    events = pd.read_parquet(EVENTS_CACHE)
    membership = pd.read_csv(ROOT / "config" / "sp500_membership.csv")
    membership["date"] = pd.to_datetime(membership["date"])
    cov = edgar.member_quarter_coverage(events, membership, "2016-01-01", "2025-12-31")
    assert cov["pct"] >= 95.0
    spy = pd.read_parquet(ROOT / "data" / "raw" / "SPY.parquet")
    assert events["day0"].isin(pd.DatetimeIndex(spy.index)).all()
    assert not events.duplicated(["ticker", "day0"]).any()


# --- Strategy ---------------------------------------------------------------

from backend.app.quant import earnings_drift as ed  # noqa: E402

COST = ed.COST_PER_SIDE


def _prices(n: int = 120, tickers=("A", "B")) -> ed.Prices:
    index = pd.bdate_range("2020-01-01", periods=n)
    flat = pd.DataFrame(100.0, index=index, columns=list(tickers))
    spy = pd.Series(100.0, index=index)
    return ed.Prices(open=flat.copy(), close=flat.copy(), spy_open=spy, spy_close=spy)


def test_rules_match_the_preregistered_design():
    assert ed.HOLD_DAYS == 60
    assert ed.ENTRY_OFFSET == 2
    assert ed.POOL_DAYS == 252
    assert ed.MAX_POSITIONS == 20
    assert ed.COST_PER_SIDE == 0.0005
    assert (ed.TOP_QUANTILE, ed.BOTTOM_QUANTILE) == (0.9, 0.1)


def test_ear_compounds_abnormal_returns_days_minus_1_to_plus_1():
    p = _prices()
    i0 = 10
    p.close.iloc[i0 - 1, 0] = 101.0  # day -1: +1%
    p.close.iloc[i0, 0] = 103.02  # day 0: +2%
    p.close.iloc[i0 + 1 :, 0] = 103.02 * 0.99  # noqa: E203 (day +1: -1%)
    spy = p.spy_close.copy()
    spy.iloc[i0 - 1 :] = 100.5  # noqa: E203 (SPY +0.5% on day -1)
    p = ed.Prices(p.open, p.close, p.spy_open, spy)
    events = pd.DataFrame({"ticker": ["A"], "day0": [p.close.index[i0]]})
    table = ed.event_table(events, p)
    expected = (1 + 0.01 - 0.005) * 1.02 * 0.99 - 1
    assert table.loc[0, "ear"] == pytest.approx(expected)
    assert table.loc[0, ["known", "entry", "exit_plan"]].tolist() == [11, 12, 71]


def test_event_table_bhar_from_entry_open_to_exit_close_vs_spy():
    p = _prices()
    p.open.iloc[12, 0] = 100.0
    p.close.iloc[71:, 0] = 110.0
    spy_close = p.spy_close.copy()
    spy_close.iloc[71:] = 102.0
    p = ed.Prices(p.open, p.close, p.spy_open, spy_close)
    events = pd.DataFrame({"ticker": ["A"], "day0": [p.close.index[10]]})
    row = ed.event_table(events, p).iloc[0]
    assert row["bhar"] == pytest.approx(0.10 - 0.02)
    assert row["exit"] == 71 and not row["delisted"] and row["complete"]


def test_event_table_delisted_exits_at_last_close():
    p = _prices()
    p.close.iloc[30, 0] = 90.0
    p.close.iloc[31:, 0] = np.nan
    events = pd.DataFrame({"ticker": ["A"], "day0": [p.close.index[10]]})
    row = ed.event_table(events, p).iloc[0]
    assert row["exit"] == 30 and row["delisted"]
    assert row["bhar"] == pytest.approx(-0.10)


def test_membership_at_day_zero():
    membership = pd.DataFrame(
        {
            "date": pd.to_datetime(["2016-01-31", "2016-02-29", "2016-02-29"]),
            "ticker": ["A", "A", "B"],
        }
    )
    events = pd.DataFrame(
        {
            "ticker": ["A", "B", "B", "B", "A"],
            "day0": pd.to_datetime(
                ["2015-06-01", "2015-06-01", "2016-01-15", "2016-02-10", "2016-03-07"]
            ),
        }
    )
    # Before the first snapshot its list is used (for the trailing pool only).
    assert ed.member_at_day0(events, membership).tolist() == [
        True,
        False,
        False,
        True,
        False,
    ]


def _table(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ear", "known", "member"])


def test_classify_uses_only_earlier_member_ears_in_the_window():
    table = _table(
        [
            (1.0, 0, True),
            (2.0, 1, True),
            (3.0, 2, True),
            (4.0, 3, True),
            (100.0, 3, False),  # not a member: not in the pool
            (10.0, 4, True),
            (0.0, 4, True),
            (5.0, 9, True),  # pool = known 4..8: two events < min_pool
        ]
    )
    out = ed.classify(table, pool_days=5, min_pool=3)
    assert out.tolist()[5:] == ["top", "bottom", None]
    assert out.iloc[4] is None  # non-members are never classified


def test_classify_never_sees_later_ears():
    base = [(float(i), i, True) for i in range(10)] + [(5.0, 10, True)]
    a = ed.classify(_table(base), pool_days=20, min_pool=3)
    b = ed.classify(_table(base + [(1000.0, 11, True)]), pool_days=20, min_pool=3)
    assert a.iloc[10] == b.iloc[10]


def _signals(p: ed.Prices, rows: list[tuple[str, int, float, str]]) -> pd.DataFrame:
    """rows: ticker, entry index, ear, signal."""
    t = pd.DataFrame(rows, columns=["ticker", "entry", "ear", "signal"])
    t["exit_plan"] = t["entry"] + ed.HOLD_DAYS - 1
    t["day0"] = p.close.index[t["entry"] - ed.ENTRY_OFFSET]
    return t


def test_simulate_long_entry_open_exit_close_with_costs():
    p = _prices()
    p.close.iloc[71:, 0] = 110.0
    result = ed.simulate(_signals(p, [("A", 12, 0.1, "top")]), p, 0, 119)
    shares = 10_000 / ed.MAX_POSITIONS / 100.0
    expected = 10_000 - shares * 100 * (1 + COST) + shares * 110 * (1 - COST)
    assert result.equity.iloc[-1] == pytest.approx(expected)
    trade = result.trades.iloc[0]
    assert trade["entry_date"] == p.close.index[12]
    assert trade["exit_date"] == p.close.index[71]
    assert trade["reason"] == ed.HOLD_EXIT
    # Held from the day +2 open through the day +61 close: 60 sessions.
    assert (result.holdings > 0).sum() == ed.HOLD_DAYS


def test_simulate_caps_positions_taking_largest_ear_first():
    tickers = [f"T{i}" for i in range(25)]
    p = _prices(tickers=tickers)
    rows = [(t, 12, float(i), "top") for i, t in enumerate(tickers)]
    result = ed.simulate(_signals(p, rows), p, 0, 119)
    held = set(result.trades["ticker"])
    assert len(held) == ed.MAX_POSITIONS
    assert held == set(tickers[5:])


def test_simulate_delisted_exit_at_last_close():
    p = _prices()
    p.close.iloc[30, 0] = 80.0
    p.close.iloc[31:, 0] = np.nan
    result = ed.simulate(_signals(p, [("A", 12, 0.1, "top")]), p, 0, 119)
    trade = result.trades.iloc[0]
    assert trade["reason"] == ed.DELISTED
    assert trade["exit_price"] == pytest.approx(80.0)
    assert result.delisted_exits == 1


def test_simulate_long_only_ignores_bottom_and_long_short_shorts_it():
    p = _prices()
    p.close.iloc[71:, 0] = 90.0
    signals = _signals(p, [("A", 12, -0.1, "bottom")])
    flat = ed.simulate(signals, p, 0, 119)
    assert flat.equity.iloc[-1] == pytest.approx(10_000)
    short = ed.simulate(signals, p, 0, 119, long_short=True)
    shares = 10_000 / ed.MAX_POSITIONS / 100.0
    expected = 10_000 + shares * 100 * (1 - COST) - shares * 90 * (1 + COST)
    assert short.equity.iloc[-1] == pytest.approx(expected)
    assert short.trades.iloc[0]["side"] == "short"
    assert short.short_exposure.max() > 0


def test_event_study_mean_and_t_stat():
    t = pd.DataFrame(
        {
            "signal": ["top", "top", "bottom", None],
            "bhar": [0.01, 0.03, -0.02, 0.5],
            "complete": [True, True, True, True],
            "day0": pd.to_datetime(
                ["2020-01-02", "2020-02-03", "2020-01-02"] * 1 + ["2020-01-02"]
            ),
        }
    )
    out = ed.event_study(t, "2020-01-01", "2020-12-31")
    assert out["top"]["n"] == 2
    assert out["top"]["mean_pct"] == pytest.approx(2.0)
    assert out["top"]["t"] == pytest.approx(2.0)
    assert out["bottom"]["n"] == 1 and out["bottom"]["t"] is None


def test_exposure_matched_spy_follows_invested_fraction():
    spy = pd.Series(
        [100.0, 110.0, 121.0], index=pd.bdate_range("2020-01-01", periods=3)
    )
    invested = pd.Series([0.5, 0.5, 0.5], index=spy.index)
    curve = ed.exposure_matched(spy, invested, 10_000)
    assert curve.iloc[-1] == pytest.approx(10_000 * 1.05 * 1.05)
