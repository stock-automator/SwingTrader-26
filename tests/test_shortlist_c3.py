"""Cycle 03 step 4: the per-strategy daily shortlist (watch-list option 1).

Seams as in `test_daily_brief_f3.py`: `scan_universe`, the earnings/split
lookups and `latest_gbp_rate` are monkeypatched on `backend.app.reports.daily`;
prices are synthetic parquet files and the scorecard a JSON file in `tmp_path`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient

from backend.app.alerts.dispatcher import TELEGRAM_MAX_CHARS, TelegramAlertChannel
from backend.app.config import Settings, get_settings
from backend.app.main import app
from backend.app.quant.portfolio import PortfolioConfig
from backend.app.quant.risk import RiskManager
from backend.app.quant.setups import DIRECTION_LONG, ScanReport, Setup, scan_ticker
from backend.app.quant.strategies import REGISTRY, build_strategy
from backend.app.quant.strategies.base import BaseStrategy
from tests.test_daily_brief_f3 import TICKERS, WED_0800, _rate
from tests.test_daily_brief_f3 import _settings as _f3_settings

TOP3 = ("zscore_mean_reversion", "supertrend_psar", "bollinger_keltner_squeeze")
ENGINE = PortfolioConfig()


def _scorecard(path: Path, rows: dict[str, float]) -> Path:
    """A scorecard.json with one long row per strategy; value = test vs SPY pp."""
    body = {
        "generated_at": "2026-11-23T21:51:18+00:00",
        "rows": [
            {
                "strategy": name,
                "variant": "long",
                "test": {
                    "start": "2023-01-03",
                    "end": "2026-11-20",
                    "total_return_pct": 46.4,
                    "spy_matched_return_pct": 46.4 - vs_spy,
                    "vs_spy_pct": vs_spy,
                    "profit_factor": 1.21,
                    "max_drawdown_pct": -19.8,
                    "trades": 457,
                },
            }
            for name, vs_spy in rows.items()
        ]
        + [{"strategy": "zscore_mean_reversion", "variant": "long_short", "test": {}}],
    }
    path.write_text(json.dumps(body))
    return path


def _settings(tmp_path, scorecard: dict[str, float] | None = None, **kw) -> Settings:
    path = tmp_path / "scorecard.json"
    if scorecard is not None:
        _scorecard(path, scorecard)
    kw.setdefault("shortlist_strategies", TOP3)
    return _f3_settings(tmp_path, scorecard_path=path, **kw)


def _key(strategy) -> str:
    return next(n for n, cls in REGISTRY.items() if type(strategy) is cls)


class StrengthScan:
    """`picks[(strategy_key, ticker)] = (strength, rr, gap)` -> one LONG."""

    def __init__(self, picks):
        self.picks = picks
        self.calls = []

    def __call__(self, frames, strategy, risk_manager, *a, **k):
        name = _key(strategy)
        self.calls.append(name)
        report = ScanReport()
        for ticker, df in frames.items():
            if (name, ticker) not in self.picks:
                continue
            strength, rr, gap = self.picks[(name, ticker)]
            entry = round(float(df["Close"].iloc[-1]), 2)
            stop = round(entry - gap, 2)
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
                    take_profit=round(entry + rr * gap, 2),
                    shares=shares,
                    risk_amount=shares * gap,
                    reward_risk_ratio=rr,
                    strength=strength,
                )
            )
        return report


@pytest.fixture
def daily(monkeypatch):
    from backend.app.reports import daily as module

    monkeypatch.setattr(module, "fetch_earnings_dates", lambda t, *a, **k: [])
    monkeypatch.setattr(module, "fetch_stock_splits", lambda t, *a, **k: [])
    monkeypatch.setattr(module, "latest_gbp_rate", lambda *a, **k: None)
    monkeypatch.setattr(module, "scan_universe", StrengthScan({}))
    return module


def _section(brief, name):
    (section,) = [s for s in brief.shortlist if s.strategy == name]
    return section


def _section_text(text: str, name: str) -> str:
    """The rendered block for `name`: from its "name: ..." header line to
    the next strategy header or the footer."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{name}:"))
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].startswith("Research only")
            or any(lines[i].startswith(f"{n}:") for n in REGISTRY)
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


# ------------------------------------------------------------------ config


class TestShortlistConfig:
    def test_default_is_the_top3_long_only(self):
        assert Settings().shortlist_strategies == TOP3
        assert all(name in REGISTRY for name in Settings().shortlist_strategies)

    def test_default_scorecard_path(self):
        assert Settings().scorecard_path == Path("data/reports/scorecard.json")

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("SHORTLIST_STRATEGIES", " supertrend_psar, kama_trend ")
        monkeypatch.setenv("SCORECARD_PATH", "/x/card.json")
        get_settings.cache_clear()
        try:
            settings = get_settings()
            assert settings.shortlist_strategies == ("supertrend_psar", "kama_trend")
            assert settings.scorecard_path == Path("/x/card.json")
        finally:
            get_settings.cache_clear()


# -------------------------------------------------------- signal strength


def _random_frame(n=400, seed=3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.bdate_range(end="2026-11-17", periods=n)
    close = pd.Series(
        100 * np.exp(np.cumsum(rng.normal(0, 0.015, n))), index=index, dtype=float
    )
    span = close * 0.01
    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close + span,
            "Low": close - span,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=index,
    )


class TestSignalStrength:
    def test_base_strategy_has_no_strength_by_default(self):
        strategy = build_strategy("moving_average_cross")
        out = strategy.generate_signals(_random_frame())
        assert strategy.signal_strength(out) is None

    @pytest.mark.parametrize(
        "name, formula",
        [
            ("zscore_mean_reversion", lambda o: -o["zscore"]),
            (
                "supertrend_psar",
                lambda o: (o["Close"] - o["supertrend"]) / o["atr"],
            ),
            (
                "bollinger_keltner_squeeze",
                lambda o: (o["Close"] - o["bb_upper"]) / o["atr"],
            ),
        ],
    )
    def test_top3_strength_formula(self, name, formula):
        strategy = build_strategy(name)
        out = strategy.generate_signals(_random_frame())
        got = strategy.signal_strength(out)
        pd.testing.assert_series_equal(
            got, formula(out), check_names=False, check_dtype=False
        )

    def test_scan_ticker_attaches_strength_to_a_long(self):
        class LastBarBuy(BaseStrategy):
            def __init__(self):
                super().__init__(name="last bar buy")

            def generate_signals(self, df):
                out = df.copy()
                out["signal"] = 0
                out.iloc[-1, out.columns.get_loc("signal")] = 1
                buy = out["signal"] == 1
                out["sl_type"] = np.where(buy, "PERCENTAGE", None)
                out["sl_value"] = np.where(buy, 0.02, np.nan)
                out["tp_type"] = np.where(buy, "PERCENTAGE", None)
                out["tp_value"] = np.where(buy, 0.06, np.nan)
                return out

            def signal_strength(self, signals):
                return pd.Series(np.arange(len(signals)) / 10, index=signals.index)

        df = _random_frame()
        setup = scan_ticker("AAA", df, LastBarBuy(), RiskManager(10_000, 0.01))
        assert setup.direction == DIRECTION_LONG
        assert setup.strength == pytest.approx((len(df) - 1) / 10)

    def test_scan_ticker_strength_none_without_method(self):
        df = _random_frame()
        setup = scan_ticker(
            "AAA", df, build_strategy("moving_average_cross"), RiskManager(1e4, 0.01)
        )
        assert setup.strength is None


# ------------------------------------------------------- the brief sections


class TestShortlistSections:
    def test_one_section_per_configured_strategy_in_order(self, daily, tmp_path):
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert [s.strategy for s in brief.shortlist] == list(TOP3)
        for name in TOP3:
            assert name in brief.text

    def test_reuses_the_brief_scan_no_extra_scan(self, daily, tmp_path):
        scan = StrengthScan({("zscore_mean_reversion", "AAA"): (2.0, 3.0, 2.0)})
        daily.scan_universe = scan
        daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert sorted(scan.calls) == sorted(REGISTRY)

    def test_ranks_by_own_strength_top_three(self, daily, tmp_path):
        z = "zscore_mean_reversion"
        daily.scan_universe = StrengthScan(
            {
                (z, "AAA"): (1.0, 3.0, 2.0),
                (z, "BBB"): (3.0, 3.0, 2.0),
                (z, "CCC"): (2.0, 3.0, 2.0),
                (z, "DDD"): (0.5, 3.0, 2.0),
                # a better R:R must not outrank a stronger signal
                (z, "EEE"): (0.1, 9.0, 2.0),
            }
        )
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        picks = _section(brief, z).picks
        assert [p.ticker for p in picks] == ["BBB", "CCC", "AAA"]
        assert [p.strength for p in picks] == [3.0, 2.0, 1.0]
        assert all(p.strategy == z for p in picks)
        block = _section_text(brief.text, z)
        assert block.index("BBB") < block.index("CCC") < block.index("AAA")
        assert "DDD" not in block

    def test_sections_are_independent(self, daily, tmp_path):
        daily.scan_universe = StrengthScan(
            {
                ("zscore_mean_reversion", "AAA"): (1.0, 3.0, 2.0),
                ("supertrend_psar", "AAA"): (1.0, 3.0, 2.0),
                ("supertrend_psar", "BBB"): (2.0, 3.0, 2.0),
            }
        )
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert [p.ticker for p in _section(brief, "zscore_mean_reversion").picks] == [
            "AAA"
        ]
        assert [p.ticker for p in _section(brief, "supertrend_psar").picks] == [
            "BBB",
            "AAA",
        ]
        assert _section(brief, "bollinger_keltner_squeeze").picks == []

    @pytest.mark.parametrize("gap", [2.0, 6.0])
    def test_sized_like_the_portfolio_engine(self, daily, tmp_path, gap):
        """1% of the engine's capital over the stop, capped at 25% notional."""
        z = "zscore_mean_reversion"
        daily.scan_universe = StrengthScan({(z, "AAA"): (1.0, 3.0, gap)})
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        (pick,) = _section(brief, z).picks
        equity = ENGINE.initial_capital
        by_risk = RiskManager(equity, ENGINE.risk_per_trade_pct).position_size(
            pick.entry, pick.stop
        )
        assert by_risk == math.floor(equity * ENGINE.risk_per_trade_pct / gap + 1e-6)
        by_cap = math.floor(equity * ENGINE.max_position_pct / pick.entry)
        assert pick.shares == min(by_risk, by_cap)
        assert pick.risk_usd == pytest.approx(pick.shares * (pick.entry - pick.stop))
        assert pick.risk_usd <= equity * ENGINE.risk_per_trade_pct + 0.01
        assert pick.shares * pick.entry <= equity * ENGINE.max_position_pct
        assert pick.target == pytest.approx(pick.entry + 3.0 * gap, abs=0.01)
        assert brief.shortlist_equity == equity

    def test_pick_lists_entry_stop_target_shares_and_both_risks(
        self, daily, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(daily, "latest_gbp_rate", lambda *a, **k: _rate(1.25))
        z = "zscore_mean_reversion"
        daily.scan_universe = StrengthScan({(z, "AAA"): (1.0, 3.0, 2.0)})
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        (pick,) = _section(brief, z).picks
        assert pick.risk_gbp == pytest.approx(pick.risk_usd / 1.25, abs=0.01)
        block = _section_text(brief.text, z)
        for value in (pick.entry, pick.stop, pick.target):
            assert f"{value:.2f}" in block
        assert f"{pick.shares} shares" in block
        assert f"${pick.risk_usd:.2f}" in block and f"£{pick.risk_gbp:.2f}" in block

    def test_live_gate_rejects_below_minimum_reward_risk(self, daily, tmp_path):
        z = "zscore_mean_reversion"
        daily.scan_universe = StrengthScan({(z, "AAA"): (5.0, 2.0, 2.0)})
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        section = _section(brief, z)
        assert section.picks == []
        assert "2.5" in section.note and "reward:risk" in section.note
        assert section.note in brief.text

    def test_earnings_lockout_and_lookup_failure_exclude(
        self, daily, tmp_path, monkeypatch
    ):
        z = "zscore_mean_reversion"

        def earnings(ticker, *a, **k):
            if ticker == "CCC":
                raise requests.ConnectionError("calendar down")
            return [pd.Timestamp("2026-11-18")] if ticker == "BBB" else []

        monkeypatch.setattr(daily, "fetch_earnings_dates", earnings)
        daily.scan_universe = StrengthScan(
            {
                (z, "AAA"): (1.0, 3.0, 2.0),
                (z, "BBB"): (3.0, 3.0, 2.0),
                (z, "CCC"): (2.0, 3.0, 2.0),
            }
        )
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert [p.ticker for p in _section(brief, z).picks] == ["AAA"]

    def test_too_wide_to_size_is_not_a_pick(self, daily, tmp_path):
        z = "zscore_mean_reversion"
        # Stop 60 below a ~110 close: 1% of $10k buys one share, risk $60 is
        # fine, but a stop wider than $100 rounds to 0 shares.
        daily.scan_universe = StrengthScan({(z, "AAA"): (1.0, 3.0, 101.0)})
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        section = _section(brief, z)
        assert section.picks == []
        assert "whole share" in section.note

    def test_no_picks_says_so_in_the_section(self, daily, tmp_path):
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        for name in TOP3:
            section = _section(brief, name)
            assert section.picks == []
            assert section.note.startswith("No picks")
            assert section.note in _section_text(brief.text, name)

    def test_regime_blocked_is_said_per_section(self, daily, tmp_path, monkeypatch):
        monkeypatch.setattr(
            daily.MacroRegimeDetector, "is_long_blocked", lambda self, df: True
        )
        monkeypatch.setattr(
            daily.MacroRegimeDetector,
            "current_regime",
            lambda self, df: "BEAR_TRENDING",
        )
        daily.scan_universe = StrengthScan(
            {(n, t): (1.0, 3.0, 2.0) for n in TOP3 for t in TICKERS}
        )
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        assert [s.strategy for s in brief.shortlist] == list(TOP3)
        for name in TOP3:
            section = _section(brief, name)
            assert section.picks == []
            assert "BEAR_TRENDING" in section.note
            assert "no new longs" in section.note.lower()
            assert section.note in _section_text(brief.text, name)

    def test_unknown_strategy_is_reported_not_dropped(self, daily, tmp_path):
        settings = _settings(tmp_path, shortlist_strategies=("nope", TOP3[0]))
        brief = daily.build_daily_brief(settings, now=WED_0800)
        assert [s.strategy for s in brief.shortlist] == ["nope", TOP3[0]]
        assert "unknown strategy" in _section(brief, "nope").note.lower()


# --------------------------------------------------------- scorecard lines


class TestShortlistScorecard:
    def test_header_has_test_return_vs_spy_pf_and_drawdown(self, daily, tmp_path):
        settings = _settings(tmp_path, scorecard={n: -42.7 for n in TOP3})
        brief = daily.build_daily_brief(settings, now=WED_0800)
        for name in TOP3:
            section = _section(brief, name)
            assert section.trails_spy is True
            for part in ("46.4%", "89.1%", "-42.7 pp", "PF 1.21", "-19.8%"):
                assert part in section.scorecard, (part, section.scorecard)
            block = _section_text(brief.text, name)
            assert section.scorecard in block
            assert "trails S&P — paper only" in block.splitlines()[0]

    def test_beating_spy_is_not_marked_trailing(self, daily, tmp_path):
        card = {TOP3[0]: 5.0, TOP3[1]: -1.0, TOP3[2]: -1.0}
        brief = daily.build_daily_brief(
            _settings(tmp_path, scorecard=card), now=WED_0800
        )
        assert _section(brief, TOP3[0]).trails_spy is False
        header = _section_text(brief.text, TOP3[0]).splitlines()[0]
        assert "paper only" in header and "trails" not in header

    def test_missing_scorecard_is_loud(self, daily, tmp_path, caplog):
        brief = daily.build_daily_brief(_settings(tmp_path), now=WED_0800)
        for name in TOP3:
            section = _section(brief, name)
            assert "NO SCORECARD YET" in section.scorecard
            assert section.trails_spy is None
            assert "NO SCORECARD YET" in _section_text(brief.text, name)
            assert "paper only" in _section_text(brief.text, name)
        assert any("scorecard" in r.getMessage().lower() for r in caplog.records)

    def test_unreadable_scorecard_is_loud(self, daily, tmp_path):
        settings = _settings(tmp_path)
        settings.scorecard_path.write_text("{not json")
        brief = daily.build_daily_brief(settings, now=WED_0800)
        assert all("NO SCORECARD YET" in s.scorecard for s in brief.shortlist)

    def test_strategy_without_a_row_is_loud(self, daily, tmp_path):
        settings = _settings(tmp_path, scorecard={TOP3[0]: -1.0})
        brief = daily.build_daily_brief(settings, now=WED_0800)
        assert "NO SCORECARD" in _section(brief, TOP3[1]).scorecard
        assert "NO SCORECARD" not in _section(brief, TOP3[0]).scorecard


# --------------------------------------------------- Telegram and the cache


class TestShortlistTextAndCache:
    def test_long_brief_splits_under_telegram_limit(self, daily, tmp_path, monkeypatch):
        names = tuple(sorted(REGISTRY))
        daily.scan_universe = StrengthScan(
            {(n, t): (float(i), 3.0, 2.0) for n in names for i, t in enumerate(TICKERS)}
        )
        monkeypatch.setattr(daily, "latest_gbp_rate", lambda *a, **k: _rate())
        settings = _settings(
            tmp_path, scorecard={n: -1.0 for n in names}, shortlist_strategies=names
        )
        brief = daily.build_daily_brief(settings, now=WED_0800)
        assert len(brief.text) > TELEGRAM_MAX_CHARS
        sent = []

        class _Ok:
            def raise_for_status(self):
                return None

        monkeypatch.setattr(
            requests, "post", lambda url, json=None, **k: sent.append(json) or _Ok()
        )
        parts = TelegramAlertChannel("T", "1").send_text(brief.text)
        assert parts == len(sent) >= 2
        assert all(len(p["text"]) <= TELEGRAM_MAX_CHARS for p in sent)
        joined = "\n".join(p["text"] for p in sent)
        for line in brief.text.splitlines():
            assert line in joined

    def test_top3_brief_fits_one_message(self, daily, tmp_path, monkeypatch):
        monkeypatch.setattr(daily, "latest_gbp_rate", lambda *a, **k: _rate())
        daily.scan_universe = StrengthScan(
            {(n, t): (1.0, 3.0, 2.0) for n in REGISTRY for t in TICKERS}
        )
        settings = _settings(tmp_path, scorecard={n: -1.0 for n in TOP3})
        brief = daily.build_daily_brief(settings, now=WED_0800)
        assert all(len(s.picks) == 3 for s in brief.shortlist)
        assert len(brief.text) <= TELEGRAM_MAX_CHARS
        last = [line for line in brief.text.splitlines() if line.strip()][-1]
        assert "Research only" in last

    def test_cached_json_and_get_daily_carry_the_shortlist(self, daily, tmp_path):
        z = "zscore_mean_reversion"
        daily.scan_universe = StrengthScan({(z, "AAA"): (1.0, 3.0, 2.0)})
        settings = _settings(tmp_path, scorecard={n: -1.0 for n in TOP3})
        app.dependency_overrides[get_settings] = lambda: settings
        try:
            client = TestClient(app)
            assert client.post("/api/v1/reports/daily/refresh").status_code == 202
            body = client.get("/api/v1/reports/daily").json()
        finally:
            app.dependency_overrides.pop(get_settings, None)
        assert body["shortlist_equity"] == ENGINE.initial_capital
        assert [s["strategy"] for s in body["shortlist"]] == list(TOP3)
        (pick,) = body["shortlist"][0]["picks"]
        assert {
            "ticker",
            "strategy",
            "entry",
            "stop",
            "target",
            "shares",
            "risk_usd",
            "risk_gbp",
            "strength",
        } <= set(pick)
        assert pick["strategy"] == z
        assert {"strategy", "scorecard", "trails_spy", "note", "picks"} <= set(
            body["shortlist"][0]
        )

    def test_old_cache_without_shortlist_still_serves(self, daily, tmp_path):
        settings = _settings(tmp_path)
        path = daily.cache_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "as_of": "2026-11-17",
                    "last_session": "2026-11-17",
                    "regime": "BULL_TRENDING",
                    "stale": False,
                    "setups": [],
                    "text": "x",
                    "computed_at": "2026-11-18T08:00:00+00:00",
                }
            )
        )
        app.dependency_overrides[get_settings] = lambda: settings
        try:
            res = TestClient(app).get("/api/v1/reports/daily")
        finally:
            app.dependency_overrides.pop(get_settings, None)
        assert res.status_code == 200
        assert res.json()["shortlist"] == []
