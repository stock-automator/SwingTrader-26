"""scripts/paper_executor.py: pure executor of the cached daily brief (HTTP mocked)."""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "paper_executor.py"
NOW = datetime(2026, 11, 23, 14, 35, tzinfo=timezone.utc)


@pytest.fixture
def ex(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("paper_executor", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE_PATH", tmp_path / "executor_state.json")
    return mod


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


def setup(t):
    return {"ticker": t, "entry": 10.0, "stop": 9.0, "target": 12.0, "shares": 5}


class FakeSession:
    def __init__(self, brief, held=(), post_status=200, down=False):
        self.brief, self.held = brief, held
        self.post_status, self.down = post_status, down
        self.posts = []

    def get(self, url, timeout=None):
        if self.down:
            raise requests.ConnectionError("down")
        if url.endswith("/reports/daily"):
            return self.brief
        return Resp(200, {"positions": [{"symbol": s} for s in self.held]})

    def post(self, url, json=None, timeout=None):
        self.posts.append(json)
        return Resp(
            self.post_status, {"detail": "Order blocked (ENTRY): market closed"}
        )


def brief(*tickers, stale=False):
    return Resp(200, {"stale": stale, "setups": [setup(t) for t in tickers]})


def test_places_bracket_and_skips_held(ex):
    s = FakeSession(brief("AAA", "BBB"), held=["BBB"])
    out = ex.run_once(session=s, now=NOW)
    assert out["placed"] == ["AAA"] and out["skipped"] == ["BBB"]
    assert s.posts == [
        {
            "ticker": "AAA",
            "qty": 5,
            "side": "buy",
            "order_type": "BRACKET",
            "stop_loss": 9.0,
            "take_profit": 12.0,
            "intent": "ENTRY",
        }
    ]


def test_422_is_blocked_not_error(ex):
    out = ex.run_once(session=FakeSession(brief("AAA"), post_status=422), now=NOW)
    assert out["placed"] == [] and "market closed" in out["blocked"][0]


def test_rerun_same_day_is_idempotent(ex):
    s = FakeSession(brief("AAA"))
    ex.run_once(session=s, now=NOW)
    out = ex.run_once(session=s, now=NOW)
    assert len(s.posts) == 1 and out["skipped"] == ["AAA"]
    assert json.loads(ex.STATE_PATH.read_text())["date"] == "2026-11-23"


def test_no_brief_or_stale_places_nothing(ex):
    for b in (Resp(404), brief("AAA", stale=True)):
        s = FakeSession(b)
        out = ex.run_once(session=s, now=NOW)
        assert s.posts == [] and out["placed"] == [] and out["reason"]


def test_backend_down_exits_nonzero(ex, monkeypatch, capsys):
    monkeypatch.setattr(ex.requests, "get", FakeSession(None, down=True).get)
    assert ex.main() != 0


# ------------------------------------- cycle 03 step 4: the daily shortlist

Z, ST = "zscore_mean_reversion", "supertrend_psar"


def pick(t, strategy=Z, risk=100.0, stop=9.0):
    return {
        "ticker": t,
        "strategy": strategy,
        "entry": 10.0,
        "stop": stop,
        "target": 13.0,
        "shares": 7,
        "risk_usd": risk,
    }


def shortlist(*sections, setups=(), equity=10_000.0, stale=False):
    return Resp(
        200,
        {
            "stale": stale,
            "setups": [setup(t) for t in setups],
            "shortlist_equity": equity,
            "shortlist": [
                {"strategy": name, "picks": list(picks)} for name, picks in sections
            ],
        },
    )


def _tags(s):
    return [p.get("client_order_id") for p in s.posts]


def test_shortlist_picks_become_tagged_brackets(ex):
    s = FakeSession(shortlist((Z, [pick("AAA")]), (ST, [pick("BBB", ST)])))
    out = ex.run_once(session=s, now=NOW)
    assert out["placed"] == ["AAA", "BBB"]
    first = s.posts[0]
    assert first["order_type"] == "BRACKET" and first["intent"] == "ENTRY"
    assert first["qty"] == 7 and first["side"] == "buy"
    assert first["stop_loss"] == 9.0 and first["take_profit"] == 13.0
    tags = _tags(s)
    assert Z in tags[0] and "AAA" in tags[0] and "2026-11-23" in tags[0]
    assert ST in tags[1] and "BBB" in tags[1]
    state = json.loads(ex.STATE_PATH.read_text())
    assert state["open"]["AAA"]["strategy"] == Z
    assert state["open"]["BBB"]["strategy"] == ST


def test_brief_setups_go_first_and_stay_untagged(ex):
    s = FakeSession(shortlist((Z, [pick("AAA")]), setups=["CCC"]))
    out = ex.run_once(session=s, now=NOW)
    assert out["placed"] == ["CCC", "AAA"]
    assert "client_order_id" not in s.posts[0]


def test_same_ticker_in_two_sections_is_placed_once(ex):
    s = FakeSession(shortlist((Z, [pick("AAA")]), (ST, [pick("AAA", ST)])))
    out = ex.run_once(session=s, now=NOW)
    assert out["placed"] == ["AAA"] and len(s.posts) == 1
    assert Z in _tags(s)[0]


def test_shortlist_rerun_same_day_is_idempotent(ex):
    s = FakeSession(shortlist((Z, [pick("AAA"), pick("BBB")])))
    ex.run_once(session=s, now=NOW)
    out = ex.run_once(session=s, now=NOW)
    assert len(s.posts) == 2 and out["placed"] == []
    assert sorted(out["skipped"]) == ["AAA", "BBB"]


def test_max_six_positions_counts_everything_held(ex):
    held = ["H1", "H2", "H3", "H4", "H5"]
    s = FakeSession(
        shortlist((Z, [pick("AAA", risk=10.0), pick("BBB", risk=10.0)])), held=held
    )
    out = ex.run_once(session=s, now=NOW)
    assert out["placed"] == ["AAA"]
    assert any("BBB" in x and "max positions" in x for x in out["limited"])


def test_heat_cap_six_percent_of_the_shortlist_equity(ex):
    picks = [pick("AAA", risk=250.0), pick("BBB", risk=250.0), pick("CCC", risk=90)]
    s = FakeSession(shortlist((Z, picks)))
    out = ex.run_once(session=s, now=NOW)
    # $600 heat on $10,000: 250 + 250 + 90 = 590 fits.
    assert out["placed"] == ["AAA", "BBB", "CCC"]
    picks = [pick("AAA", risk=250.0), pick("BBB", risk=250.0), pick("CCC", risk=250)]
    s = FakeSession(shortlist((Z, picks)))
    ex.STATE_PATH.unlink()
    out = ex.run_once(session=s, now=NOW)
    assert out["placed"] == ["AAA", "BBB"]
    assert any("CCC" in x and "heat" in x for x in out["limited"])


def test_heat_counts_known_open_risk_from_earlier_days(ex):
    ex.STATE_PATH.write_text(
        json.dumps(
            {
                "date": "2026-11-20",
                "tickers": ["OLD"],
                "open": {"OLD": {"strategy": Z, "risk_usd": 550.0}},
            }
        )
    )
    s = FakeSession(shortlist((Z, [pick("AAA", risk=100.0)])), held=["OLD"])
    out = ex.run_once(session=s, now=NOW)
    assert out["placed"] == [] and "heat" in out["limited"][0]


def test_unknown_held_position_counts_one_percent_heat(ex):
    held = ["U1", "U2", "U3", "U4", "U5"]  # 5 x 1% of $10k = $500 assumed
    s = FakeSession(shortlist((Z, [pick("AAA", risk=150.0)])), held=held)
    out = ex.run_once(session=s, now=NOW)
    assert out["placed"] == [] and "heat" in out["limited"][0]


def test_closed_positions_leave_the_open_book(ex):
    ex.STATE_PATH.write_text(
        json.dumps(
            {
                "date": "2026-11-20",
                "tickers": ["OLD"],
                "open": {"OLD": {"strategy": Z, "risk_usd": 550.0}},
            }
        )
    )
    s = FakeSession(shortlist((Z, [pick("AAA", risk=100.0)])))
    out = ex.run_once(session=s, now=NOW)
    assert out["placed"] == ["AAA"]
    assert set(json.loads(ex.STATE_PATH.read_text())["open"]) == {"AAA"}


def test_shortlist_without_equity_fails_closed(ex):
    s = FakeSession(shortlist((Z, [pick("AAA")]), equity=0))
    out = ex.run_once(session=s, now=NOW)
    assert s.posts == [] and "equity" in out["reason"]


def test_stale_brief_places_no_shortlist(ex):
    s = FakeSession(shortlist((Z, [pick("AAA")]), stale=True))
    out = ex.run_once(session=s, now=NOW)
    assert s.posts == [] and out["reason"] == "stale brief"


def test_blocked_pick_is_reported_and_not_counted(ex):
    s = FakeSession(shortlist((Z, [pick("AAA")])), post_status=422)
    out = ex.run_once(session=s, now=NOW)
    assert out["placed"] == [] and "AAA" in out["blocked"][0]
    state = json.loads(ex.STATE_PATH.read_text()) if ex.STATE_PATH.exists() else {}
    assert "AAA" not in state.get("open", {})
