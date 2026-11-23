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
