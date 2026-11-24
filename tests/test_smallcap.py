"""Cycle 07b small/mid caps: broad survivorship-free universe from Alpaca's
assets, resumable bar download, EDGAR common-stock classification, monthly
eligibility, the pre-registered signals and the tiered-cost simulator."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from backend.app.data import broad_universe as bu

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
