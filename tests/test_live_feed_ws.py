"""
Tests for `WS /ws/v1/live-feed` (`backend.app.api.ws`).

The three event builders (`_build_regime_event`/`_build_signal_event`/
`_build_order_update_event`) are monkeypatched directly - each already has
its own coverage via `test_market_regime_engine.py`/`test_screener*.py`/
`test_orders_api.py`. This suite is only about the socket's own framing and
polling behavior: does it push a regime frame then a signal frame on
connect, does it skip the order-update frame when nothing changed, etc.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.app.api.ws as ws_module
from backend.app.config import Settings
from backend.app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr(
        ws_module, "get_settings", lambda: Settings(ws_poll_seconds=0.05)
    )


@pytest.fixture(autouse=True)
def _stub_builders(monkeypatch):
    monkeypatch.setattr(
        ws_module,
        "_build_regime_event",
        lambda settings: {"type": "regime", "state": "CAUTION_CHOP"},
    )
    monkeypatch.setattr(
        ws_module,
        "_build_signal_event",
        lambda strategy, account_equity, risk_pct, blackout, settings: {
            "type": "signal",
            "setups": [],
            "scanned": 0,
        },
    )
    monkeypatch.setattr(ws_module, "_build_order_update_event", lambda since: None)


class TestLiveFeedSocket:
    def test_pushes_regime_then_signal_frame_on_connect(self, client):
        with client.websocket_connect("/ws/v1/live-feed") as websocket:
            first = websocket.receive_json()
            second = websocket.receive_json()

        assert first["type"] == "regime"
        assert first["state"] == "CAUTION_CHOP"
        assert second["type"] == "signal"

    def test_no_order_update_frame_when_nothing_changed(self, client):
        with client.websocket_connect("/ws/v1/live-feed") as websocket:
            websocket.receive_json()  # regime
            websocket.receive_json()  # signal
            # Second round starts immediately (poll interval is 0.05s) -
            # still just regime/signal, no order_update frame in between.
            third = websocket.receive_json()

        assert third["type"] == "regime"

    def test_order_update_frame_is_pushed_when_present(self, client, monkeypatch):
        monkeypatch.setattr(
            ws_module,
            "_build_order_update_event",
            lambda since: {
                "type": "order_update",
                "orders": [{"order_id": "abc", "status": "FILLED"}],
            },
        )

        with client.websocket_connect("/ws/v1/live-feed") as websocket:
            websocket.receive_json()  # regime
            websocket.receive_json()  # signal
            third = websocket.receive_json()

        assert third["type"] == "order_update"
        assert third["orders"][0]["order_id"] == "abc"

    def test_signal_scan_error_is_sent_as_error_frame_not_disconnect(
        self, client, monkeypatch
    ):
        from fastapi import HTTPException

        def _boom(*args, **kwargs):
            raise HTTPException(status_code=422, detail="unknown strategy")

        monkeypatch.setattr(ws_module, "_build_signal_event", _boom)

        with client.websocket_connect("/ws/v1/live-feed") as websocket:
            websocket.receive_json()  # regime
            error_frame = websocket.receive_json()

        assert error_frame["type"] == "error"
        assert "unknown strategy" in error_frame["detail"]
