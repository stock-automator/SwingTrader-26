"""Batch 02 F3 part A: the daily Telegram brief.

Seams (all monkeypatched on `backend.app.reports.daily`, never the network):
`scan_universe` (canned setups, sized by the brief's own RiskManager),
`fetch_earnings_dates` / `fetch_stock_splits`, `latest_gbp_rate`. Prices are
synthetic parquet files in `tmp_path`; Telegram HTTP is a fake `requests.post`.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.data.fx import FxRate
from backend.app.main import app
from backend.app.quant.setups import (
    DIRECTION_EXIT,
    DIRECTION_LONG,
    DIRECTION_SHORT,
    ScanReport,
    Setup,
)

UK = ZoneInfo("Europe/London")
TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE"]
WED_0800 = datetime(2026, 11, 18, 8, 0, tzinfo=UK)  # last completed: Tue 11-17
TELEGRAM_LIMIT = 4096


def _bars(last_day: str, n: int = 300, start: float = 50.0) -> pd.DataFrame:
    index = pd.bdate_range(end=last_day, periods=n)
    close = pd.Series([start + i * 0.2 for i in range(n)], index=index)
    return pd.DataFrame(
        {
            "Open": close - 0.1,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": [2_000_000] * n,
        },
        index=index,
    )


def _settings(tmp_path, last_day="2026-11-17", tickers=TICKERS, **kw) -> Settings:
    data = tmp_path / "raw"
    data.mkdir(exist_ok=True)
    for i, ticker in enumerate([*tickers, "SPY"]):
        _bars(last_day, start=50.0 + i).to_parquet(data / f"{ticker}.parquet")
    watchlist = tmp_path / "watchlist.txt"
    watchlist.write_text("\n".join(tickers) + "\n")
    return Settings(
        data_dir=data,
        allow_downloads=False,
        watchlist_path=watchlist,
        journal_path=tmp_path / "trades.csv",
        alert_config_path=tmp_path / "alerts_channels.json",
        **kw,
    )


class FakeScan:
    """Stands in for `quant.setups.scan_universe`: one LONG per qualifying
    ticker that was loaded, sized with the RiskManager the brief passed in."""

    def __init__(self, qualify, stop_gap=None, extra=()):
        self.qualify = list(qualify)
        self.stop_gap = stop_gap or {}
        self.extra = list(extra)
        self.risk_managers = []

    def __call__(self, frames, strategy, risk_manager, *args, **kwargs):
        self.risk_managers.append(risk_manager)
        report = ScanReport()
        for ticker in self.qualify:
            if ticker not in frames:
                continue
            df = frames[ticker]
            entry = round(float(df["Close"].iloc[-1]), 2)
            stop = round(entry - self.stop_gap.get(ticker, 2.0), 2)
            shares = risk_manager.position_size(entry, stop)
            report.setups.append(
                Setup(
                    ticker=ticker,
                    direction=DIRECTION_LONG,
                    tradable=shares > 0,
                    as_of=df.index[-1],
                    close=entry,
                    regime="BULL_TREND",
                    adx=30.0,
                    atr=1.0,
                    entry_price=entry,
                    stop_loss=stop,
                    take_profit=round(entry + 3 * (entry - stop), 2),
                    shares=shares,
                    risk_amount=shares * (entry - stop),
                )
            )
        report.setups.extend(self.extra)
        return report


@pytest.fixture
def daily(monkeypatch):
    from backend.app.reports import daily as module

    monkeypatch.setattr(module, "fetch_earnings_dates", lambda t, *a, **k: [])
    monkeypatch.setattr(module, "fetch_stock_splits", lambda t, *a, **k: [], False)
    monkeypatch.setattr(module, "latest_gbp_rate", lambda *a, **k: None)
    monkeypatch.setattr(module, "scan_universe", FakeScan([]))
    return module


def _rate(value=1.25) -> FxRate:
    return FxRate(value, datetime(2026, 11, 18, 7, 59), "live")


def _footer_ok(text: str) -> bool:
    last = [line for line in text.splitlines() if line.strip()][-1]
    return "Research only" in last and "paper" in last


# --------------------------------------------------------------- the brief


class TestF3Setups:
    def test_f3_at_most_three_setups_even_if_five_qualify(self, daily, tmp_path):
        daily.scan_universe = FakeScan(TICKERS)
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        tickers = [s.ticker for s in brief.setups]
        assert len(tickers) == 3
        assert len(set(tickers)) == 3 and set(tickers) <= set(TICKERS)

    def test_f3_setup_fields_and_whole_share_sizing(self, daily, tmp_path):
        scan = FakeScan(["AAA"])
        daily.scan_universe = scan
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        (setup,) = brief.setups
        expected = scan.risk_managers[-1].position_size(setup.entry, setup.stop)
        assert isinstance(setup.shares, int) and setup.shares == expected > 0
        assert setup.risk_usd == pytest.approx(
            setup.shares * (setup.entry - setup.stop), abs=0.01
        )
        assert setup.target > setup.entry > setup.stop
        assert setup.strategy and isinstance(setup.strategy, str)
        assert setup.reason and "\n" not in setup.reason

    def test_f3_zero_share_setup_is_not_listed(self, daily, tmp_path):
        """A stop too wide for the account sizes to 0 whole shares: no trade."""
        daily.scan_universe = FakeScan(["AAA", "BBB"], stop_gap={"BBB": 40.0})
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert [s.ticker for s in brief.setups] == ["AAA"]

    def test_f3_short_and_exit_rows_are_never_setups(self, daily, tmp_path):
        common = dict(
            tradable=False,
            as_of=pd.Timestamp("2026-11-17"),
            close=10.0,
            regime="BEAR_TREND",
            adx=None,
            atr=None,
            entry_price=10.0,
        )
        extra = [
            Setup(ticker="DDD", direction=DIRECTION_SHORT, **common),
            Setup(ticker="EEE", direction=DIRECTION_EXIT, **common),
        ]
        daily.scan_universe = FakeScan([], extra=extra)
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert brief.setups == []

    def test_f3_earnings_inside_lockout_excluded(self, daily, tmp_path, monkeypatch):
        soon = {"AAA": [pd.Timestamp("2026-11-18")]}
        far = {"BBB": [pd.Timestamp("2027-01-20")]}
        monkeypatch.setattr(
            daily,
            "fetch_earnings_dates",
            lambda t, *a, **k: soon.get(t) or far.get(t) or [],
        )
        daily.scan_universe = FakeScan(["AAA", "BBB"])
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert [s.ticker for s in brief.setups] == ["BBB"]

    def test_f3_earnings_lookup_error_fails_closed(self, daily, tmp_path, monkeypatch):
        def boom(ticker, *a, **k):
            raise requests.ConnectionError("calendar down")

        monkeypatch.setattr(daily, "fetch_earnings_dates", boom)
        daily.scan_universe = FakeScan(["AAA"])
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert brief.setups == []
        assert brief.text.startswith("No trades today")


class TestF3NoTrades:
    def test_f3_no_setups_says_no_trades_with_a_reason(self, daily, tmp_path):
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert brief.setups == []
        first = brief.text.splitlines()[0]
        assert first.startswith("No trades today")
        assert len(first) > len("No trades today") + 3, first
        assert brief.regime and isinstance(brief.regime, str)
        assert _footer_ok(brief.text)

    def test_f3_all_blocked_by_earnings_reason_mentions_earnings(
        self, daily, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            daily,
            "fetch_earnings_dates",
            lambda t, *a, **k: [pd.Timestamp("2026-11-18")],
        )
        daily.scan_universe = FakeScan(["AAA", "BBB"])
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert brief.text.startswith("No trades today")
        assert "earnings" in brief.text.lower()

    def test_f3_empty_universe_is_no_trades_not_an_error(self, daily, tmp_path):
        brief = daily.build_daily_brief(_settings(tmp_path, tickers=[]), now=WED_0800)
        assert brief.text.startswith("No trades today")
        assert _footer_ok(brief.text)


class TestF3Staleness:
    @pytest.mark.parametrize(
        "now, last_bar, stale",
        [
            (WED_0800, "2026-11-17", False),
            (WED_0800, "2026-11-16", True),
            # Monday 08:00 UK: Friday is the last completed session.
            (datetime(2026, 11, 16, 8, 0, tzinfo=UK), "2026-11-13", False),
            (datetime(2026, 11, 16, 8, 0, tzinfo=UK), "2026-11-12", True),
            # Wed 16:00 UK = 11:00 New York: today's session isn't complete.
            (datetime(2026, 11, 18, 16, 0, tzinfo=UK), "2026-11-17", False),
            # DST gap week: UK on GMT, New York still on EDT.
            (datetime(2026, 10, 27, 8, 0, tzinfo=UK), "2026-10-26", False),
            (datetime(2026, 10, 27, 8, 0, tzinfo=UK), "2026-10-23", True),
        ],
    )
    def test_f3_stale_flag_vs_last_completed_nyse_session(
        self, daily, tmp_path, now, last_bar, stale
    ):
        daily.scan_universe = FakeScan(["AAA"])
        brief = daily.build_daily_brief(_settings(tmp_path, last_day=last_bar), now=now)
        assert brief.as_of == pd.Timestamp(last_bar).date()
        assert brief.stale is stale
        assert ("data stale" in brief.text.lower()) is stale


class TestF3Text:
    def test_f3_plain_text_footer_and_length(self, daily, tmp_path, monkeypatch):
        monkeypatch.setattr(daily, "latest_gbp_rate", lambda *a, **k: _rate())
        daily.scan_universe = FakeScan(TICKERS)
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert len(brief.text) < TELEGRAM_LIMIT
        assert not re.search(r"[*`]|\[.*\]\(", brief.text), "no Markdown"
        assert _footer_ok(brief.text)
        for setup in brief.setups:
            assert setup.ticker in brief.text
            assert str(setup.shares) in brief.text

    def test_f3_gbp_risk_when_fx_available(self, daily, tmp_path, monkeypatch):
        monkeypatch.setattr(daily, "latest_gbp_rate", lambda *a, **k: _rate(1.25))
        daily.scan_universe = FakeScan(["AAA"])
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        (setup,) = brief.setups
        assert setup.risk_gbp == pytest.approx(setup.risk_usd / 1.25, abs=0.01)
        assert "£" in brief.text and "£ n/a" not in brief.text

    def test_f3_gbp_na_when_fx_missing(self, daily, tmp_path):
        daily.scan_universe = FakeScan(["AAA"])
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert brief.setups[0].risk_gbp is None
        assert "£ n/a" in brief.text


# ------------------------------------------------------------ the endpoints


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.pop(get_settings, None)


class _Resp:
    def __init__(self, status: int = 200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Bad Request")


def _use(settings):
    app.dependency_overrides[get_settings] = lambda: settings


class TestF3Endpoints:
    def test_f3_get_daily_returns_brief_json(self, daily, tmp_path, client):
        daily.scan_universe = FakeScan(["AAA"])
        _use(_settings(tmp_path))
        res = client.get("/api/v1/reports/daily")
        assert res.status_code == 200, res.text
        body = res.json()
        assert {"as_of", "setups", "regime", "stale", "text"} <= set(body)
        setup = body["setups"][0]
        assert {
            "ticker",
            "strategy",
            "entry",
            "stop",
            "target",
            "shares",
            "risk_usd",
            "risk_gbp",
            "reason",
        } <= set(setup)

    def test_f3_send_posts_plain_text_once(self, daily, tmp_path, client, monkeypatch):
        calls = []
        monkeypatch.setattr(
            requests, "post", lambda url, **k: calls.append((url, k)) or _Resp()
        )
        _use(_settings(tmp_path, telegram_bot_token="T", telegram_chat_id="1"))
        res = client.post("/api/v1/reports/daily/send")
        assert res.status_code == 200, res.text
        assert res.json()["sent"] is True and res.json()["parts"] == 1
        assert len(calls) == 1
        payload = calls[0][1]["json"]
        assert "parse_mode" not in payload
        assert payload["text"].startswith("No trades today")

    def test_f3_send_telegram_400_is_reported_not_500(
        self, daily, tmp_path, client, monkeypatch
    ):
        monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(400))
        _use(_settings(tmp_path, telegram_bot_token="T", telegram_chat_id="1"))
        res = client.post("/api/v1/reports/daily/send")
        assert res.status_code == 200, res.text
        assert res.json()["sent"] is False
        assert "400" in res.json()["error"]

    def test_f3_send_network_error_is_reported_not_500(
        self, daily, tmp_path, client, monkeypatch
    ):
        def down(*a, **k):
            raise requests.ConnectionError("network down")

        monkeypatch.setattr(requests, "post", down)
        _use(_settings(tmp_path, telegram_bot_token="T", telegram_chat_id="1"))
        res = client.post("/api/v1/reports/daily/send")
        assert res.status_code == 200, res.text
        assert res.json()["sent"] is False and res.json()["error"]

    def test_f3_send_without_telegram_is_503(self, daily, tmp_path, client):
        _use(_settings(tmp_path))
        res = client.post("/api/v1/reports/daily/send")
        assert res.status_code == 503
        assert "telegram" in res.json()["detail"].lower()


# ---------------------------------------------------- TelegramAlertChannel


class TestF3TelegramChannel:
    def _capture(self, monkeypatch, statuses=None):
        sent = []
        statuses = list(statuses or [])

        def fake_post(url, json=None, timeout=None, **k):
            sent.append(json)
            return _Resp(statuses.pop(0) if statuses else 200)

        monkeypatch.setattr(requests, "post", fake_post)
        return sent

    def test_f3_send_is_plain_text(self, monkeypatch):
        from backend.app.alerts.dispatcher import AlertMessage, TelegramAlertChannel

        sent = self._capture(monkeypatch)
        TelegramAlertChannel("T", "1").send(AlertMessage(title="t_1", body="b*2"))
        assert "parse_mode" not in sent[0]

    def test_f3_short_text_is_one_part(self, monkeypatch):
        from backend.app.alerts.dispatcher import TelegramAlertChannel

        sent = self._capture(monkeypatch)
        assert TelegramAlertChannel("T", "1").send_text("hello\nworld") == 1
        assert sent[0]["text"] == "hello\nworld"

    def test_f3_long_text_splits_on_newlines_into_numbered_parts(self, monkeypatch):
        from backend.app.alerts.dispatcher import TelegramAlertChannel

        sent = self._capture(monkeypatch)
        lines = [f"line {i:04d} " + "x" * 60 for i in range(150)]  # ~10.5k chars
        parts = TelegramAlertChannel("T", "1").send_text("\n".join(lines))
        texts = [p["text"] for p in sent]
        assert parts == len(texts) >= 3
        for i, text in enumerate(texts, start=1):
            assert len(text) <= TELEGRAM_LIMIT
            assert f"({i}/{parts})" in text
        joined = "\n".join(texts)
        for line in lines:  # every line survives whole, in order
            assert line in joined
        assert [joined.index(line) for line in lines] == sorted(
            joined.index(line) for line in lines
        )

    def test_f3_single_overlong_line_still_fits(self, monkeypatch):
        from backend.app.alerts.dispatcher import TelegramAlertChannel

        sent = self._capture(monkeypatch)
        TelegramAlertChannel("T", "1").send_text("y" * 9000)
        assert len(sent) >= 3
        assert all(len(p["text"]) <= TELEGRAM_LIMIT for p in sent)
        assert sum(p["text"].count("y") for p in sent) == 9000

    def test_f3_any_part_failing_raises(self, monkeypatch):
        from backend.app.alerts.dispatcher import TelegramAlertChannel

        self._capture(monkeypatch, statuses=[200, 400])
        with pytest.raises(requests.HTTPError):
            TelegramAlertChannel("T", "1").send_text("z" * 5000 + "\n" + "z" * 100)
