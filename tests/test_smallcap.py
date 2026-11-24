"""Cycle 07b small/mid caps: broad survivorship-free universe from Alpaca's
assets, resumable bar download, EDGAR common-stock classification, monthly
eligibility, the pre-registered signals and the tiered-cost simulator."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.app.data import broad_universe as bu
from backend.app.quant import earnings_drift as ed
from backend.app.quant import smallcap as sc
from backend.app.quant.momentum import build_panel

ROOT = Path(__file__).resolve().parent.parent

# --- Assets ------------------------------------------------------------------


def _assets(rows: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["symbol", "name", "exchange", "status"])


def test_candidates_keep_exchange_and_otc_stocks_and_drop_obvious_non_commons():
    df = _assets(
        [
            ("AAPL", "Apple Inc. Common Stock", "NASDAQ", "active"),
            ("NBL", "Noble Energy Inc. Common Stock", "NASDAQ", "inactive"),
            ("BRK.B", "Berkshire Hathaway Inc. Class B", "NYSE", "active"),
            ("BBBYQ", "Bed Bath & Beyond Inc", "OTC", "inactive"),
            ("SPY", "SPDR S&P 500 ETF Trust", "ARCA", "active"),
            ("HTZWW", "Hertz Global Holdings, Inc Warrant", "NASDAQ", "active"),
            ("IPOE.U", "Social Capital Units, each consisting", "NYSE", "inactive"),
            ("ALLY.PRA", "GMAC Capital Trust Preferred", "NYSE", "inactive"),
            ("ORSNR", "Orisun Acquisition Corp. Right", "NASDAQ", "inactive"),
            ("829RGT026", "Sintx rights (Expire 10/12/22)", "NASDAQ", "inactive"),
            ("NSRGF", "Nestle SA Ordinary Shares", "OTC", "active"),
            ("TIIAY", "TELECOM ITALIA SPONSORED ADR", "OTC", "inactive"),
        ]
    )
    out = bu.candidates(df)
    assert out["symbol"].tolist() == ["AAPL", "NBL", "BRK.B", "BBBYQ"]
    assert out["listed"].tolist() == [True, True, True, False]


def test_file_name_uses_the_repo_symbol_form():
    assert bu.file_symbol("BRK.B") == "BRK-B"
    assert bu.file_symbol("AAPL") == "AAPL"


# --- Bars download -------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: dict | str):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self._body


def bar(day: str, close: float, volume: float = 1000.0) -> dict:
    return {
        "t": f"{day}T05:00:00Z",
        "o": close - 1,
        "h": close + 1,
        "l": close - 2,
        "c": close,
        "v": volume,
    }


class FakeAlpaca:
    """Two pages for symbols A,B; one page for anything else."""

    def __init__(self, fail_on: str | None = None):
        self.calls: list[dict] = []
        self.fail_on = fail_on

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append(dict(url=url, params=dict(params or {}), headers=headers))
        symbols = params["symbols"].split(",")
        if self.fail_on and self.fail_on in symbols:
            return FakeResponse(403, "forbidden for key SECRETKEY")
        if symbols[:2] == ["A", "B"] and not params.get("page_token"):
            return FakeResponse(
                200,
                {
                    "bars": {"A": [bar("2016-01-04", 10), bar("2016-01-05", 11)]},
                    "next_page_token": "p2",
                },
            )
        if params.get("page_token") == "p2":
            return FakeResponse(
                200,
                {
                    "bars": {
                        "A": [bar("2016-01-06", 12)],
                        "B": [bar("2016-01-04", 50)],
                    },
                    "next_page_token": None,
                },
            )
        return FakeResponse(200, {"bars": {}, "next_page_token": None})


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _downloader(http, clock=None, **kw) -> bu.BarsDownloader:
    clock = clock or FakeClock()
    return bu.BarsDownloader(
        key="KEYID",
        secret="SECRETKEY",
        http_get=http,
        sleep=clock.sleep,
        clock=clock.clock,
        **kw,
    )


def test_fetch_batch_is_one_multi_symbol_request_per_page_sip_all_adjusted():
    http = FakeAlpaca()
    bars = _downloader(http).fetch_batch(["A", "B", "C"])
    assert len(http.calls) == 2
    first = http.calls[0]["params"]
    assert first["symbols"] == "A,B,C"
    assert first["feed"] == "sip"
    assert first["adjustment"] == "all"
    assert first["timeframe"] == "1Day"
    assert first["start"] == bu.START
    assert http.calls[1]["params"]["page_token"] == "p2"
    assert bars["A"]["Close"].tolist() == [10.0, 11.0, 12.0]
    assert bars["B"]["Close"].tolist() == [50.0]
    assert bars["C"].empty
    assert list(bars["A"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert bars["A"].index[0] == pd.Timestamp("2016-01-04")


def test_requests_are_spaced_to_stay_under_200_a_minute():
    clock = FakeClock()
    http = FakeAlpaca()
    dl = _downloader(http, clock=clock)
    dl.fetch_batch(["A", "B"])
    dl.fetch_batch(["C"])
    assert len(http.calls) == 3
    assert 60.0 / bu.MIN_INTERVAL_SECONDS <= 200
    # Every request after the first waited the full interval (clock is frozen
    # apart from the sleeps).
    assert clock.sleeps == pytest.approx([bu.MIN_INTERVAL_SECONDS] * 2)


def test_download_writes_loader_files_and_resumes_without_refetching(tmp_path):
    http = FakeAlpaca()
    dl = _downloader(http, batch_size=2)
    status = dl.download(["A", "B", "BRK.B"], tmp_path)
    assert status["A"]["bars"] == 3 and status["BRK.B"]["bars"] == 0
    saved = pd.read_parquet(tmp_path / "A.parquet")
    assert saved["Close"].tolist() == [10.0, 11.0, 12.0]
    assert (saved["Adj Close"] == saved["Close"]).all()
    assert not (tmp_path / "BRK-B.parquet").exists()  # no bars, no file
    calls = len(http.calls)

    again = _downloader(http, batch_size=2).download(["A", "B", "BRK.B"], tmp_path)
    assert len(http.calls) == calls  # all done: nothing refetched
    assert again == status


def test_download_error_is_scrubbed_and_not_marked_done(tmp_path, caplog):
    http = FakeAlpaca(fail_on="C")
    dl = _downloader(http, batch_size=1)
    status = dl.download(["C", "D"], tmp_path)
    assert "C" not in status and "D" in status
    assert "SECRETKEY" not in caplog.text
    assert "403" in caplog.text
    headers = http.calls[0]["headers"]
    assert headers["APCA-API-KEY-ID"] == "KEYID"


def test_raw_broad_is_gitignored():
    result = subprocess.run(
        ["git", "check-ignore", "-q", "data/raw_broad/AAPL.parquet"], cwd=ROOT
    )
    assert result.returncode == 0


# --- EDGAR classification ------------------------------------------------------


def _hit(cik: int, items=("2.02",)) -> dict:
    return {"_source": {"ciks": [f"{cik:010d}"], "items": list(items)}}


class FakeEdgar:
    """`company_tickers`, `submission`, `filings` and `fulltext_hits`."""

    def __init__(self):
        self.current = {"AAPL": 320193, "XLF": 999, "ABC": 555}
        self.sic = {320193: "3571", 999: "6221", 777: "7372", 555: "1000", 888: ""}
        self.forms = {
            320193: [("10-K", "2020-10-30"), ("8-K", "2020-07-30")],
            999: [("10-K", "2020-02-01")],
            777: [("10-Q", "2018-05-01"), ("8-K", "2018-04-25")],
            # Today's owner of ABC began filing after the old ABC stopped.
            555: [("10-K", "2024-03-01")],
            888: [("20-F", "2019-04-01"), ("6-K", "2019-05-01")],
        }
        self.hits = {"OLD": [_hit(777)] * 3, "ABC": [_hit(888)] * 3}
        self.fts_calls: list[str] = []

    def company_tickers(self):
        return dict(self.current)

    def submission(self, name: str) -> dict:
        cik = int(name.removeprefix("CIK").removesuffix(".json"))
        return {"sic": self.sic[cik]}

    def filings(self, cik: int, since: str = "2015-01-01") -> pd.DataFrame:
        rows = self.forms[cik]
        return pd.DataFrame(
            {
                "form": [f for f, _ in rows],
                "items": [""] * len(rows),
                "acceptanceDateTime": [f"{d}T21:00:00.000Z" for _, d in rows],
                "filingDate": [d for _, d in rows],
            }
        )

    def fulltext_hits(self, symbol: str, start: str, end: str) -> list[dict]:
        self.fts_calls.append(symbol)
        return self.hits.get(symbol, [])


def _spans(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ticker", "first", "last"]).assign(
        first=lambda d: pd.to_datetime(d["first"]),
        last=lambda d: pd.to_datetime(d["last"]),
    )


def test_classify_uses_current_owner_fulltext_and_sic_and_reports_unresolved():
    spans = _spans(
        [
            ("AAPL", "2016-01-04", "2026-11-20"),
            ("XLF", "2016-01-04", "2026-11-20"),  # commodity-pool SIC
            ("OLD", "2016-01-04", "2018-06-01"),  # delisted: full-text search
            ("OLDQ", "2016-01-04", "2018-06-01"),  # bankruptcy suffix: OLD
            ("ABC", "2016-01-04", "2019-06-01"),  # reused symbol, 20-F filer
            ("NONE", "2016-01-04", "2017-01-01"),
        ]
    )
    out = bu.classify(spans, FakeEdgar()).set_index("ticker")
    assert out.loc["AAPL", "source"] == "current"
    assert bool(out.loc["AAPL", "common"]) is True
    assert bool(out.loc["XLF", "common"]) is False
    assert out.loc["XLF", "reason"] == "fund SIC 6221"
    assert out.loc["OLD", "cik"] == 777 and out.loc["OLD", "source"] == "fulltext"
    assert bool(out.loc["OLD", "common"]) is True
    assert out.loc["OLDQ", "cik"] == 777
    # Today's ABC (555) filed nothing while the old ABC had bars, so full-text
    # search finds the old one, a 20-F filer: not a common-stock 10-K filer.
    assert out.loc["ABC", "cik"] == 888
    assert out.loc["ABC", "reason"] == "no 10-K/10-Q"
    assert bool(out.loc["ABC", "common"]) is False
    assert out.loc["NONE", "source"] == "unresolved"
    assert bool(out.loc["NONE", "common"]) is False
    assert out.loc["NONE", "reason"] == "no CIK"


def test_classification_summary_counts_classified_and_unresolved():
    out = bu.classify(
        _spans(
            [
                ("AAPL", "2016-01-04", "2026-11-20"),
                ("XLF", "2016-01-04", "2026-11-20"),
                ("NONE", "2016-01-04", "2017-01-01"),
            ]
        ),
        FakeEdgar(),
    )
    s = bu.classification_summary(out)
    assert s == {"symbols": 3, "resolved": 2, "unresolved": 1, "common": 1}


def test_classify_logs_an_edgar_error_and_keeps_going(caplog):
    from backend.app.data.edgar import EdgarError

    client = FakeEdgar()

    def broken(symbol, start, end):
        raise EdgarError("HTTP 500")

    client.fulltext_hits = broken
    out = bu.classify(
        _spans(
            [("OLD", "2016-01-04", "2018-06-01"), ("AAPL", "2016-01-04", "2026-11-20")]
        ),
        client,
    ).set_index("ticker")
    assert out.loc["OLD", "reason"] == "EDGAR error"
    assert bool(out.loc["AAPL", "common"]) is True
    assert "OLD" in caplog.text and "HTTP 500" in caplog.text


# --- Panel, gaps and the spliced-bar filter ----------------------------------

IDX = pd.bdate_range("2020-01-01", periods=60)


def ohlcv(close, volume=1_000_000.0, index=IDX) -> pd.DataFrame:
    close = pd.Series(close, index=index[: len(close)], dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close,
            "Low": close,
            "Close": close,
            "Volume": volume,
        }
    ).dropna()


def spy(index=IDX) -> pd.DataFrame:
    return ohlcv(np.full(len(index), 300.0), index=index)


def test_panel_drops_spliced_tickers_with_the_portfolio_filter():
    spliced = np.full(60, 100.0)
    spliced[30] = 4.0  # 100 -> 4 -> 100: a spliced bar
    panel = sc.build_universe_panel(
        {"GOOD": ohlcv(np.full(60, 50.0)), "BAD": ohlcv(spliced)}, spy()
    )
    assert panel.tickers == ["GOOD"]
    assert panel.suspect == ["BAD"]


def test_segment_age_restarts_after_a_long_gap():
    close = pd.DataFrame({"X": np.arange(1.0, 61.0)}, index=IDX)
    close.iloc[10:13, 0] = np.nan  # 3-session halt: same segment
    close.iloc[20 : 20 + sc.MAX_GAP + 1, 0] = np.nan  # long gap: new company
    age = sc.segment_age(close)["X"]
    assert age.iloc[9] == 10
    assert age.iloc[13] == 14  # the short halt does not restart the count
    first_back = 20 + sc.MAX_GAP + 1
    assert age.iloc[first_back] == 1
    assert age.iloc[first_back + 4] == 5


def test_returns_are_not_computed_across_a_symbol_reuse_gap():
    close = np.full(60, 10.0)
    close[25:36] = np.nan  # 11 sessions with no bar
    close[36:] = 80.0  # another company took the symbol
    panel = sc.build_universe_panel({"X": ohlcv(close)}, spy())
    r1 = sc.return_1m(panel)["X"]
    assert np.isnan(r1.iloc[36 + sc.R1_DAYS - 1])
    assert r1.iloc[36 + sc.R1_DAYS] == pytest.approx(0.0)
    assert r1.iloc[24] == pytest.approx(0.0)


# --- Eligibility ------------------------------------------------------------------


def _eligibility_panel():
    frames = {
        # ADV = close * volume.
        "BIG1": ohlcv(np.full(60, 100.0), volume=1_000_000),  # $100M, top
        "BIG2": ohlcv(np.full(60, 90.0), volume=1_000_000),  # $90M, top
        "MID": ohlcv(np.full(60, 50.0), volume=200_000),  # $10M
        "CHEAP": ohlcv(np.full(60, 4.0), volume=5_000_000),  # $20M, < $5
        "THIN": ohlcv(np.full(60, 20.0), volume=100_000),  # $2M
        "SMALL": ohlcv(np.full(60, 10.0), volume=600_000),  # $6M
    }
    return sc.build_universe_panel(frames, spy())


def test_eligibility_needs_price_dollar_volume_and_excludes_the_largest():
    panel = _eligibility_panel()
    elig = sc.eligible(panel, exclude_top=2)
    day = elig.iloc[30]
    assert sorted(day[day].index) == ["MID", "SMALL"]
    # The first ADV_DAYS - 1 sessions have no 20-day average yet.
    assert not elig.iloc[sc.ADV_DAYS - 2].any()


def test_monthly_eligibility_counts_and_summary():
    panel = _eligibility_panel()
    elig = sc.eligible(panel, exclude_top=2)
    counts = sc.monthly_counts(elig)
    # Month ends of IDX (Jan, Feb; March is unfinished): Jan has 23 sessions.
    assert counts.index.tolist() == [pd.Timestamp("2020-01-31"), pd.Timestamp("2020-02-28")]
    assert counts.tolist() == [2, 2]
    assert sc.count_summary(counts) == {"min": 2, "median": 2.0, "max": 2}


def test_delisted_share_counts_eligible_names_whose_bars_end_early():
    frames = {
        "ALIVE": ohlcv(np.full(60, 10.0), volume=600_000),
        "GONE": ohlcv(np.full(40, 10.0), volume=600_000),
        "NEVER": ohlcv(np.full(30, 1.0), volume=600_000),  # never eligible
    }
    panel = sc.build_universe_panel(frames, spy())
    elig = sc.eligible(panel, exclude_top=0)
    out = sc.delisted_share(panel, elig)
    assert out == {"ever_eligible": 2, "delisted": 1, "pct": 50.0}


# --- Signals ----------------------------------------------------------------------


def test_selections_reversal_momentum_and_equal_weight():
    names = [f"S{k}" for k in range(20)]
    elig = pd.Series(True, index=names)
    elig["S19"] = False
    r1 = pd.Series(np.arange(20.0), index=names)  # S0 lowest
    r61 = pd.Series(-np.arange(20.0), index=names)  # S0 highest
    assert sc.lowest_decile(r1, elig) == {"S0": 1.0}  # 19 eligible -> 1 name
    assert sc.highest_decile(r61, elig) == {"S0": 1.0}
    ew = sc.equal_weight(elig)
    assert len(ew) == 19 and ew["S1"] == pytest.approx(1 / 19)
    assert sc.top_n(pd.Series([3.0, 1.0, 2.0], index=list("abc")), 2) == {
        "a": 0.5,
        "c": 0.5,
    }


def test_composite_is_the_mean_cross_sectional_zscore():
    names = ["A", "B", "C", "D"]
    elig = pd.Series([True, True, True, False], index=names)
    r1 = pd.Series([0.10, -0.10, 0.0, -0.5], index=names)
    r61 = pd.Series([0.30, 0.10, 0.20, 0.9], index=names)
    ear = pd.Series([0.0, 0.0, 0.06, 0.2], index=names)
    score = sc.composite_score(r1, r61, ear, elig)
    assert list(score.index) == ["A", "B", "C"]

    def z(x):
        x = np.asarray(x, dtype=float)
        return (x - x.mean()) / x.std(ddof=0)

    want = (z([-0.10, 0.10, 0.0]) + z([0.30, 0.10, 0.20]) + z([0, 0, 0.06])) / 3
    assert score.to_numpy() == pytest.approx(want)


def test_composite_needs_both_returns_and_survives_a_constant_column():
    elig = pd.Series(True, index=list("ABC"))
    r1 = pd.Series([0.1, np.nan, 0.0], index=list("ABC"))
    r61 = pd.Series([0.1, 0.2, 0.3], index=list("ABC"))
    ear = pd.Series(0.0, index=list("ABC"))  # no EAR within 60 days: all 0
    score = sc.composite_score(r1, r61, ear, elig)
    assert list(score.index) == ["A", "C"]
    assert np.isfinite(score).all()


def test_latest_ear_holds_for_60_sessions_then_zero():
    index = pd.bdate_range("2020-01-01", periods=100)
    table = pd.DataFrame(
        {"ticker": ["A", "A", "B"], "known": [5, 10, 7], "ear": [0.1, -0.2, 0.3]}
    )
    out = sc.latest_ear(table, index, ["A", "B", "C"])
    assert out["A"].iloc[4] == 0.0
    assert out["A"].iloc[5] == pytest.approx(0.1)
    assert out["A"].iloc[10] == pytest.approx(-0.2)
    assert out["A"].iloc[10 + sc.EAR_DAYS - 1] == pytest.approx(-0.2)
    assert out["A"].iloc[10 + sc.EAR_DAYS] == 0.0
    assert out["B"].iloc[7 + sc.EAR_DAYS - 1] == pytest.approx(0.3)
    assert (out["C"] == 0).all()


# --- Costs and the monthly simulator --------------------------------------------------


def test_cost_tiers_use_the_previous_close_dollar_volume():
    adv = pd.DataFrame({"X": [30e6, 10e6, np.nan], "Y": [5e6, 25e6, 25e6]})
    rates = sc.cost_rates(adv)
    assert rates["X"].tolist()[1:] == pytest.approx([0.0010, 0.0020])
    assert rates["Y"].tolist()[1:] == pytest.approx([0.0020, 0.0010])
    assert sc.cost_rates(adv, mult=2.0)["X"].iloc[1] == pytest.approx(0.0020)
    assert rates.iloc[0].tolist() == pytest.approx([0.0020, 0.0020])


def _sim_panel():
    # 2020-01-31 (position 22) is a month end with a full 20-day ADV.
    index = pd.bdate_range("2020-01-01", periods=45)
    a = np.full(45, 10.0)
    a[24:] = 11.0  # +10% from 2020-02-04
    frames = {
        "A": ohlcv(a, volume=10_000_000, index=index),  # $100M: 10bp
        "B": ohlcv(np.full(45, 10.0), volume=100_000, index=index),  # $1M: 20bp
    }
    return sc.build_universe_panel(frames, spy(index)), index


def test_monthly_simulator_trades_at_the_next_open_with_tiered_costs():
    panel, index = _sim_panel()
    feb3 = index.get_loc(pd.Timestamp("2020-02-03"))

    def pick(i):
        return {"A": 0.5, "B": 0.5}

    res = sc.simulate_monthly(panel, pick, "2020-02-03", "2020-02-28")
    buys = res.trades[res.trades["side"] == "buy"].set_index("ticker")
    assert set(buys.index) == {"A", "B"}
    assert (buys["date"] == index[feb3]).all()
    # 10bp on A (liquid), 20bp on B, on 5000 each of 10k.
    assert buys.loc["A", "cost"] == pytest.approx(5000 / 1.001 * 0.001, rel=1e-3)
    assert buys.loc["B", "cost"] == pytest.approx(5000 / 1.002 * 0.002, rel=1e-3)
    assert res.equity.iloc[-1] == pytest.approx(
        10_000 / 2 / 1.001 * 1.1 + 10_000 / 2 / 1.002, rel=1e-6
    )
    double = sc.simulate_monthly(panel, pick, "2020-02-03", "2020-02-28", mult=2.0)
    assert double.equity.iloc[-1] < res.equity.iloc[-1]


def test_monthly_simulator_sells_a_delisted_holding_at_its_last_close():
    index = pd.bdate_range("2020-01-01", periods=45)
    frames = {
        "A": ohlcv(np.full(26, 10.0), volume=10_000_000, index=index),  # to 02-05
        "B": ohlcv(np.full(45, 10.0), volume=10_000_000, index=index),
    }
    panel = sc.build_universe_panel(frames, spy(index))
    res = sc.simulate_monthly(panel, lambda i: {"A": 1.0}, "2020-02-03", "2020-02-28")
    assert res.delisted_exits == 1
    assert res.holdings.iloc[-1] == 0
    assert res.equity.iloc[-1] == pytest.approx(10_000 / 1.001 * 0.999, rel=1e-6)


def test_stats_add_calmar_turnover_and_average_holdings():
    panel, _ = _sim_panel()
    res = sc.simulate_monthly(
        panel, lambda i: {"A": 0.5, "B": 0.5}, "2020-02-03", "2020-02-28"
    )
    st = sc.stats(res)
    for key in (
        "cagr_pct",
        "max_drawdown_pct",
        "sharpe",
        "calmar",
        "positive_years_pct",
        "turnover_annual_x",
        "avg_holdings",
    ):
        assert key in st
    years = (res.equity.index[-1] - res.equity.index[0]).days / 365.25
    traded = res.trades["notional"].sum()
    want = traded / 2 / res.equity.mean() / years
    assert st["turnover_annual_x"] == pytest.approx(want, rel=1e-3)
    assert st["avg_holdings"] == pytest.approx(res.holdings.mean(), abs=0.01)


# --- Earnings drift with tiered costs (additive `costs` matrix) -----------------------


def _ed_prices():
    index = pd.bdate_range("2020-01-01", periods=40)
    close = pd.DataFrame({"A": np.linspace(10, 12, 40)}, index=index)
    spy_s = pd.Series(300.0, index=index)
    return ed.Prices(open=close, close=close, spy_open=spy_s, spy_close=spy_s)


def _ed_signal():
    return pd.DataFrame(
        {
            "ticker": ["A"],
            "entry": [5],
            "exit_plan": [20],
            "ear": [0.1],
            "signal": [ed.TOP],
            "day0": [pd.Timestamp("2020-01-06")],
        }
    )


def test_earnings_simulate_costs_matrix_matches_the_scalar_when_constant():
    prices = _ed_prices()
    flat = np.full(prices.close.shape, 0.001)
    a = ed.simulate(_ed_signal(), prices, 0, 39, cost=0.001)
    b = ed.simulate(_ed_signal(), prices, 0, 39, costs=flat)
    assert b.equity.to_numpy() == pytest.approx(a.equity.to_numpy())
    assert b.trades["cost"].tolist() == pytest.approx(a.trades["cost"].tolist())


def test_earnings_simulate_costs_matrix_charges_the_rate_of_each_trade_day():
    prices = _ed_prices()
    rates = np.full(prices.close.shape, 0.001)
    rates[5] = 0.002  # entry day
    res = ed.simulate(_ed_signal(), prices, 0, 39, costs=rates)
    t = res.trades.iloc[0]
    entry_fee = t["shares"] * t["entry_price"] * 0.002
    exit_fee = t["shares"] * t["exit_price"] * 0.001
    assert t["cost"] == pytest.approx(entry_fee + exit_fee)


# --- Long-short diagnostic ------------------------------------------------------------


def test_spread_returns_are_long_minus_short_month_end_to_month_end():
    index = pd.bdate_range("2020-01-27", periods=30)  # Jan 31, Feb 28 month ends
    up = np.full(30, 10.0)
    up[5:] = 11.0  # +10% over February
    down = np.full(30, 10.0)
    down[5:] = 9.5  # -5%
    frames = {
        "UP": ohlcv(up, volume=10_000_000, index=index),
        "DOWN": ohlcv(down, volume=10_000_000, index=index),
    }
    panel = sc.build_universe_panel(frames, spy(index))
    jan31 = index.get_loc(pd.Timestamp("2020-01-31"))
    spread = sc.spread_returns(
        panel, lambda i: ["UP"], lambda i: ["DOWN"], "2020-01-01", "2020-03-31"
    )
    assert spread.index.tolist() == [index[jan31]]
    assert spread.iloc[0] == pytest.approx(0.10 + 0.05)
    summary = sc.spread_summary(spread)
    assert summary["months"] == 1 and summary["mean_pct"] == pytest.approx(15.0)
