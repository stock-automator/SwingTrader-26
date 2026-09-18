"""Tests for `POST /api/v1/alerts/dispatch`."""

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_settings, None)


class TestDispatchAlert:
    def test_no_channels_configured_returns_empty_results(self, client):
        app.dependency_overrides[get_settings] = lambda: Settings()

        response = client.post(
            "/api/v1/alerts/dispatch",
            json={"title": "Test", "body": "hello"},
        )

        assert response.status_code == 200
        assert response.json() == {"results": {}}

    def test_dispatches_to_configured_webhook_channel(self, client, monkeypatch):
        calls = {}

        class _FakeResponse:
            def raise_for_status(self):
                pass

        def fake_post(url, json=None, timeout=None):
            calls["url"] = url
            calls["json"] = json
            return _FakeResponse()

        import backend.app.alerts.dispatcher as dispatcher_module

        monkeypatch.setattr(dispatcher_module.requests, "post", fake_post)
        app.dependency_overrides[get_settings] = lambda: Settings(
            generic_webhook_url="https://example.com/hook"
        )

        response = client.post(
            "/api/v1/alerts/dispatch",
            json={
                "title": "LONG setup: AAPL",
                "body": "Breakout",
                "ticker": "AAPL",
                "direction": "LONG",
            },
        )

        assert response.status_code == 200
        assert response.json() == {"results": {"webhook": "sent"}}
        assert calls["url"] == "https://example.com/hook"

    def test_missing_required_fields_is_422(self, client):
        response = client.post("/api/v1/alerts/dispatch", json={"title": "Test"})
        assert response.status_code == 422
