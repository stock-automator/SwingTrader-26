"""Batch 02 F3 part A: the daily Telegram brief.

Seams (all monkeypatched on `backend.app.reports.daily`, never the network):
`scan_universe` (canned setups, sized by the brief's own RiskManager),
`fetch_earnings_dates` / `fetch_stock_splits`, `latest_gbp_rate`. Prices are
synthetic parquet files in `tmp_path`; Telegram HTTP is a fake `requests.post`.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
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


# ======================================================= rework loop 1 (F3a)

SECRET = "123456:SECRETtokenXYZ"
TG_URL = f"https://api.telegram.org/bot{SECRET}/sendMessage"


def _long_scan_setup(df, ticker, rr, gap=2.0, shares=None, risk_manager=None):
    entry = round(float(df["Close"].iloc[-1]), 2)
    stop = round(entry - gap, 2)
    n = shares if shares is not None else risk_manager.position_size(entry, stop)
    return Setup(
        ticker=ticker,
        direction=DIRECTION_LONG,
        tradable=n > 0,
        as_of=df.index[-1],
        close=entry,
        regime="BULL_TREND",
        adx=30.0,
        atr=1.0,
        entry_price=entry,
        stop_loss=stop,
        take_profit=round(entry + rr * gap, 2),
        shares=n,
        risk_amount=n * gap,
        reward_risk_ratio=rr,
    )


class RRScan:
    """`rr[(strategy_key, ticker)]` -> reward:risk of that LONG (absent: none)."""

    def __init__(self, rr):
        self.rr = rr

    def __call__(self, frames, strategy, risk_manager, *a, **k):
        from backend.app.quant.strategies import REGISTRY

        key = next(n for n, cls in REGISTRY.items() if type(strategy) is cls)
        report = ScanReport()
        for ticker, df in frames.items():
            if (key, ticker) in self.rr:
                report.setups.append(
                    _long_scan_setup(
                        df, ticker, self.rr[(key, ticker)], risk_manager=risk_manager
                    )
                )
        return report


class TestF3BoundedHistory:
    """The brief evaluates only the last SIGNAL_LOOKBACK_BARS bars."""

    def test_f3_lookback_constant_is_sane(self, daily):
        n = daily.SIGNAL_LOOKBACK_BARS
        assert isinstance(n, int) and n >= 500

    def test_f3_scan_receives_at_most_lookback_bars(self, daily, tmp_path):
        settings = _settings(tmp_path)
        for i, t in enumerate(TICKERS):
            _bars("2026-11-17", n=1500, start=50.0 + i).to_parquet(
                settings.data_dir / f"{t}.parquet"
            )
        seen = []

        def spy_scan(frames, strategy, rm, *a, **k):
            seen.extend(len(df) for df in frames.values())
            return ScanReport()

        daily.scan_universe = spy_scan
        daily.build_daily_brief(settings, now=WED_0800)
        assert seen and max(seen) <= daily.SIGNAL_LOOKBACK_BARS
        assert max(seen) > 300  # not trimmed to nothing

    @pytest.mark.parametrize(
        "name",
        sorted(
            __import__("backend.app.quant.strategies", fromlist=["REGISTRY"]).REGISTRY
        ),
    )
    def test_f3_last_bars_signal_matches_full_history(self, daily, name):
        """Latest-bar signal/sl/tp on the last-N slice == on full history, for
        several price shapes and several end points. Failure names the
        strategy that needs a larger SIGNAL_LOOKBACK_BARS."""
        import numpy as np

        from backend.app.quant.strategies import REQUIRES_BENCHMARK, build_strategy

        n_bars, lookback = 1600, daily.SIGNAL_LOOKBACK_BARS
        index = pd.bdate_range(end="2026-11-17", periods=n_bars)

        def frame(close):
            close = pd.Series(close, index=index)
            rng = np.random.default_rng(7)
            span = close * (0.004 + 0.01 * rng.random(n_bars))
            return pd.DataFrame(
                {
                    "Open": close.shift(1).fillna(close.iloc[0]),
                    "High": close + span,
                    "Low": close - span,
                    "Close": close,
                    "Volume": (1_000_000 * (1 + rng.random(n_bars))).astype(int),
                },
                index=index,
            )

        t = np.arange(n_bars)
        rng = np.random.default_rng(1)
        shapes = {
            "uptrend_noise": 50
            * np.exp(0.0006 * t + np.cumsum(rng.normal(0, 0.012, n_bars))),
            "sine_range": 100 + 12 * np.sin(t / 9.0) + rng.normal(0, 0.8, n_bars),
            "downtrend": 300
            * np.exp(-0.0005 * t + np.cumsum(rng.normal(0, 0.01, n_bars))),
            "base_then_breakout": np.where(
                t < n_bars - 60,
                80 + rng.normal(0, 1, n_bars),
                80 + (t - (n_bars - 60)) * 0.9,
            ),
        }
        bench = frame(
            100 * np.exp(0.0003 * t + np.cumsum(rng.normal(0, 0.008, n_bars)))
        )

        def tail_rows(strategy, df):
            # The latest bar plus the 7 before it (each still has >= N-7 bars
            # of history in the slice), so a quiet last bar can't hide a gap.
            out = strategy.generate_signals(df).iloc[-8:]
            return [
                (int(r["signal"]), float(r["sl_value"]), float(r["tp_value"]))
                for _, r in out.iterrows()
            ]

        def close_enough(a, b):
            return (math.isnan(a) and math.isnan(b)) or math.isclose(
                a, b, rel_tol=1e-6, abs_tol=1e-12
            )

        mismatches = []
        for shape, close in shapes.items():
            full = frame(close)
            for cut in (0, 17, 34):
                part = full.iloc[: n_bars - cut]
                strategy = build_strategy(name)
                if name in REQUIRES_BENCHMARK:
                    strategy.set_benchmark(bench)
                want = tail_rows(strategy, part)
                got = tail_rows(strategy, part.iloc[-lookback:])
                same = all(
                    w[0] == g[0]
                    and close_enough(w[1], g[1])
                    and close_enough(w[2], g[2])
                    for w, g in zip(want, got)
                )
                if not same:
                    mismatches.append((shape, cut, want[-1], got[-1]))
        assert not mismatches, (
            f"{name}: last-{lookback}-bar slice differs from full history "
            f"(raise SIGNAL_LOOKBACK_BARS): {mismatches[:3]}"
        )


class TestF3TokenScrub:
    def _boom(self, monkeypatch, exc_type=requests.ConnectionError):
        def post(url, **k):
            raise exc_type(f"HTTPSConnectionPool: Max retries, url: {TG_URL}")

        monkeypatch.setattr(requests, "post", post)

    def test_f3_send_text_error_has_no_secret(self, monkeypatch):
        from backend.app.alerts.dispatcher import TelegramAlertChannel

        self._boom(monkeypatch)
        with pytest.raises(requests.RequestException) as info:
            TelegramAlertChannel(SECRET, "1").send_text("hi")
        assert SECRET not in str(info.value) and "<token>" in str(info.value)

    def test_f3_send_error_has_no_secret(self, monkeypatch):
        from backend.app.alerts.dispatcher import AlertMessage, TelegramAlertChannel

        self._boom(monkeypatch)
        with pytest.raises(requests.RequestException) as info:
            TelegramAlertChannel(SECRET, "1").send(AlertMessage(title="t", body="b"))
        assert SECRET not in str(info.value)

    def test_f3_http_error_status_url_scrubbed(self, monkeypatch):
        from backend.app.alerts.dispatcher import TelegramAlertChannel

        class Bad:
            def raise_for_status(self):
                raise requests.HTTPError(
                    f"400 Client Error: Bad Request for url: {TG_URL}"
                )

        monkeypatch.setattr(requests, "post", lambda *a, **k: Bad())
        with pytest.raises(requests.HTTPError) as info:
            TelegramAlertChannel(SECRET, "1").send_text("hi")
        assert SECRET not in str(info.value) and "400" in str(info.value)

    def test_f3_dispatch_result_has_no_secret(self, monkeypatch):
        from backend.app.alerts.dispatcher import AlertDispatcher, AlertMessage

        self._boom(monkeypatch)
        dispatcher = AlertDispatcher.from_channel_config(
            {"telegram_bot_token": SECRET, "telegram_chat_id": "1"}
        )
        results = dispatcher.dispatch(AlertMessage(title="t", body="b"))
        assert results["telegram"].startswith("failed")
        assert SECRET not in results["telegram"]

    def test_f3_alerts_test_502_detail_has_no_secret(
        self, tmp_path, client, monkeypatch
    ):
        self._boom(monkeypatch)
        _use(_settings(tmp_path, telegram_bot_token=SECRET, telegram_chat_id="1"))
        res = client.post("/api/v1/alerts/test", json={"channel": "telegram"})
        assert res.status_code == 502
        assert SECRET not in res.text and "<token>" in res.text

    def test_f3_daily_send_error_has_no_secret(
        self, daily, tmp_path, client, monkeypatch
    ):
        self._boom(monkeypatch)
        _use(_settings(tmp_path, telegram_bot_token=SECRET, telegram_chat_id="1"))
        res = client.post("/api/v1/reports/daily/send")
        assert res.status_code == 200 and res.json()["sent"] is False
        assert SECRET not in res.text


class TestF3PartialSend:
    def _long_brief(self, daily, monkeypatch):
        text = "\n".join(["a" * 3000, "b" * 3000, "c" * 3000])
        brief = daily.DailyBrief(
            as_of=date(2026, 11, 17),
            last_session=date(2026, 11, 17),
            regime="BULL_TRENDING",
            stale=False,
            text=text,
        )
        monkeypatch.setattr(daily, "build_daily_brief", lambda *a, **k: brief)

    def test_f3_part_two_of_three_fails_reports_one_part_sent(
        self, daily, tmp_path, client, monkeypatch
    ):
        self._long_brief(daily, monkeypatch)
        posted = []

        def post(url, json=None, **k):
            posted.append(json)
            return _Resp(400 if len(posted) == 2 else 200)

        monkeypatch.setattr(requests, "post", post)
        _use(_settings(tmp_path, telegram_bot_token="T", telegram_chat_id="1"))
        res = client.post("/api/v1/reports/daily/send")
        assert res.status_code == 200, res.text
        assert len(posted) == 2  # stops at the failure
        assert res.json()["sent"] is False
        assert res.json()["parts"] == 1
        assert "400" in res.json()["error"]

    def test_f3_first_part_fails_reports_zero_parts(
        self, daily, tmp_path, client, monkeypatch
    ):
        self._long_brief(daily, monkeypatch)
        monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(400))
        _use(_settings(tmp_path, telegram_bot_token="T", telegram_chat_id="1"))
        res = client.post("/api/v1/reports/daily/send")
        assert res.json()["sent"] is False and res.json()["parts"] == 0


class TestF3OfflineCalendar:
    @pytest.mark.parametrize(
        "now, expected",
        [
            # Thanksgiving Thu 11-26 closed; Fri 11-27 03:00 NY not yet closed.
            (datetime(2026, 11, 27, 8, 0, tzinfo=UK), date(2026, 11, 25)),
            # Fri 11-27 after the 16:00 regular close (offline has no half-days).
            (datetime(2026, 11, 27, 22, 0, tzinfo=UK), date(2026, 11, 27)),
            # Thanksgiving morning and evening: Wed is still the last session.
            (datetime(2026, 11, 26, 22, 0, tzinfo=UK), date(2026, 11, 25)),
            # Good Friday 2026-04-03: Monday morning -> Thursday.
            (datetime(2026, 4, 6, 8, 0, tzinfo=UK), date(2026, 4, 2)),
            # July 4 2026 is a Saturday: observed Fri 07-03 -> Thu 07-02.
            (datetime(2026, 7, 6, 8, 0, tzinfo=UK), date(2026, 7, 2)),
            # Juneteenth Fri 2026-06-19.
            (datetime(2026, 6, 22, 8, 0, tzinfo=UK), date(2026, 6, 18)),
            # New Year's Day 2027 is a Friday.
            (datetime(2027, 1, 4, 8, 0, tzinfo=UK), date(2026, 12, 31)),
            # Christmas Fri 2026-12-25.
            (datetime(2026, 12, 28, 8, 0, tzinfo=UK), date(2026, 12, 24)),
            # Plain weekend.
            (datetime(2026, 11, 21, 12, 0, tzinfo=UK), date(2026, 11, 20)),
        ],
    )
    def test_f3_offline_last_session(self, daily, now, expected):
        assert daily.last_completed_session(now) == expected

    def test_f3_build_uses_offline_rules_without_alpaca_keys(self, daily, tmp_path):
        daily.scan_universe = FakeScan(["AAA"])
        brief = daily.build_daily_brief(
            _settings(tmp_path, last_day="2026-11-25"),
            now=datetime(2026, 11, 27, 8, 0, tzinfo=UK),
        )
        assert brief.last_session == date(2026, 11, 25) and brief.stale is False


class TestF3AlpacaCalendar:
    NOW = datetime(2026, 11, 27, 19, 0, tzinfo=UK)  # 14:00 NY, after a 13:00 close

    def _alpaca(self, tmp_path):
        return _settings(
            tmp_path,
            last_day="2026-11-25",
            alpaca_api_key="k",
            alpaca_api_secret="s",
        )

    def test_f3_alpaca_half_day_close_is_used(self, daily, tmp_path, monkeypatch):
        from backend.app.execution.market_clock import NY

        asked = []

        class Clock:
            def close_on(self, day):
                asked.append(day)
                if day.weekday() >= 5 or day == date(2026, 11, 26):
                    return None
                hour = 13 if day == date(2026, 11, 27) else 16
                return datetime(day.year, day.month, day.day, hour, 0, tzinfo=NY)

        monkeypatch.setattr(daily, "_clock_for", lambda *a, **k: Clock())
        brief = daily.build_daily_brief(self._alpaca(tmp_path), now=self.NOW)
        assert asked
        assert brief.last_session == date(2026, 11, 27)
        assert brief.stale is True and "data stale" in brief.text.lower()

    def test_f3_alpaca_failure_falls_back_to_offline(
        self, daily, tmp_path, monkeypatch
    ):
        class Clock:
            def close_on(self, day):
                raise RuntimeError("alpaca 503")

        monkeypatch.setattr(daily, "_clock_for", lambda *a, **k: Clock())
        brief = daily.build_daily_brief(self._alpaca(tmp_path), now=self.NOW)
        # Offline: 11-27 closes 16:00 NY, not yet reached -> Wed 11-25.
        assert brief.last_session == date(2026, 11, 25)
        assert brief.stale is False

    def test_f3_alpaca_not_consulted_without_keys(self, daily, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("Alpaca clock used without keys")

        monkeypatch.setattr(daily, "_clock_for", boom)
        daily.build_daily_brief(_settings(tmp_path), now=WED_0800)


class TestF3NoTradesReasons:
    def test_f3_regime_block_reason(self, daily, tmp_path):
        settings = _settings(tmp_path)
        n = 300
        index = pd.bdate_range(end="2026-11-17", periods=n)
        close = pd.Series([400.0 - i * 0.8 for i in range(n)], index=index)
        pd.DataFrame(
            {
                "Open": close,
                "High": close + 1,
                "Low": close - 1,
                "Close": close,
                "Volume": 1_000_000,
            },
            index=index,
        ).to_parquet(settings.data_dir / "SPY.parquet")
        scan = FakeScan(TICKERS)
        daily.scan_universe = scan
        brief = daily.build_daily_brief(settings, now=WED_0800)
        assert brief.setups == [] and brief.text.startswith("No trades today")
        assert "regime" in brief.text.splitlines()[0].lower()
        assert scan.risk_managers == []  # blocked before any scan

    def test_f3_circuit_breaker_reason(self, daily, tmp_path, monkeypatch):
        monkeypatch.setattr(
            daily.MacroRegimeDetector, "is_long_blocked", lambda s, d: False
        )
        monkeypatch.setattr(daily.CircuitBreaker, "is_triggered", lambda s, eq: True)
        scan = FakeScan(TICKERS)
        daily.scan_universe = scan
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        first = brief.text.splitlines()[0]
        assert brief.setups == [] and first.startswith("No trades today")
        assert "circuit breaker" in first.lower()
        assert scan.risk_managers == []

    def test_f3_too_wide_reason(self, daily, tmp_path):
        daily.scan_universe = FakeScan(
            ["AAA", "BBB"], stop_gap={"AAA": 40.0, "BBB": 40.0}
        )
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        first = brief.text.splitlines()[0]
        assert brief.setups == [] and first.startswith("No trades today")
        assert "too wide" in first and "2" in first


class TestF3Ranking:
    def test_f3_ranked_by_reward_risk_across_tickers(self, daily, tmp_path):
        rr = {
            ("donchian_breakout", "AAA"): 1.5,
            ("donchian_breakout", "BBB"): 4.0,
            ("donchian_breakout", "CCC"): 2.5,
            ("donchian_breakout", "DDD"): 3.5,
            ("donchian_breakout", "EEE"): 1.1,
        }
        daily.scan_universe = RRScan(rr)
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert [s.ticker for s in brief.setups] == ["BBB", "DDD", "CCC"]

    def test_f3_ticker_uses_highest_reward_risk_strategy_not_first(
        self, daily, tmp_path
    ):
        from backend.app.quant.strategies import REGISTRY

        keys = list(REGISTRY)
        first, last = keys[0], keys[-1]
        daily.scan_universe = RRScan(
            {(first, "AAA"): 1.5, (keys[3], "AAA"): 2.0, (last, "AAA"): 4.0}
        )
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        (setup,) = brief.setups
        assert setup.ticker == "AAA" and setup.strategy == last
        assert setup.target == pytest.approx(
            setup.entry + 4.0 * (setup.entry - setup.stop), abs=0.02
        )

    def test_f3_ticker_listed_once_across_strategies(self, daily, tmp_path):
        from backend.app.quant.strategies import REGISTRY

        keys = list(REGISTRY)
        daily.scan_universe = RRScan({(k, "AAA"): 1.0 + i for i, k in enumerate(keys)})
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert [s.ticker for s in brief.setups] == ["AAA"]
