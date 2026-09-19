"""Tests for `/api/v1/alerts/*`."""

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


@pytest.fixture
def alert_settings(tmp_path):
    settings = Settings(alert_config_path=tmp_path / "alerts_channels.json")
    app.dependency_overrides[get_settings] = lambda: settings
    return settings


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


class TestAlertConfig:
    def test_get_config_with_nothing_saved_is_all_unconfigured(
        self, client, alert_settings
    ):
        response = client.get("/api/v1/alerts/config")
        assert response.status_code == 200
        body = response.json()
        assert body["telegram_configured"] is False
        assert body["discord_configured"] is False
        assert body["webhook_configured"] is False

    def test_put_then_get_round_trips(self, client, alert_settings):
        put_response = client.put(
            "/api/v1/alerts/config",
            json={
                "discord_webhook_url": "https://discord.example/hook",
                "generic_webhook_url": "https://example.com/hook",
            },
        )
        assert put_response.status_code == 200
        assert put_response.json()["discord_configured"] is True
        assert put_response.json()["telegram_configured"] is False

        get_response = client.get("/api/v1/alerts/config")
        body = get_response.json()
        assert body["discord_webhook_url"] == "https://discord.example/hook"
        assert body["webhook_configured"] is True

        assert alert_settings.alert_config_path.exists()

    def test_put_replaces_whole_config(self, client, alert_settings):
        client.put(
            "/api/v1/alerts/config",
            json={"discord_webhook_url": "https://discord.example/hook"},
        )
        client.put(
            "/api/v1/alerts/config",
            json={"generic_webhook_url": "https://example.com/hook"},
        )
        body = client.get("/api/v1/alerts/config").json()
        assert body["discord_webhook_url"] is None
        assert body["generic_webhook_url"] == "https://example.com/hook"

    def test_saved_config_overrides_env_settings(self, client, tmp_path):
        settings = Settings(
            alert_config_path=tmp_path / "alerts_channels.json",
            discord_webhook_url="https://env.example/hook",
        )
        app.dependency_overrides[get_settings] = lambda: settings

        client.put(
            "/api/v1/alerts/config",
            json={"discord_webhook_url": "https://saved.example/hook"},
        )
        body = client.get("/api/v1/alerts/config").json()
        assert body["discord_webhook_url"] == "https://saved.example/hook"


class TestSendTestAlert:
    def test_unconfigured_channel_is_422(self, client, alert_settings):
        response = client.post("/api/v1/alerts/test", json={"channel": "telegram"})
        assert response.status_code == 422

    def test_invalid_channel_is_422(self, client, alert_settings):
        response = client.post("/api/v1/alerts/test", json={"channel": "sms"})
        assert response.status_code == 422

    def test_sends_to_configured_webhook_channel(
        self, client, alert_settings, monkeypatch
    ):
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

        client.put(
            "/api/v1/alerts/config",
            json={"generic_webhook_url": "https://example.com/hook"},
        )
        response = client.post("/api/v1/alerts/test", json={"channel": "webhook"})

        assert response.status_code == 200
        assert response.json() == {"channel": "webhook", "status": "sent"}
        assert calls["url"] == "https://example.com/hook"
        assert calls["json"]["title"] == "SwingTrader Test Alert"

    def test_channel_send_failure_is_502(self, client, alert_settings, monkeypatch):
        import requests

        import backend.app.alerts.dispatcher as dispatcher_module

        def fake_post(url, json=None, timeout=None):
            raise requests.ConnectionError("boom")

        monkeypatch.setattr(dispatcher_module.requests, "post", fake_post)

        client.put(
            "/api/v1/alerts/config",
            json={"generic_webhook_url": "https://example.com/hook"},
        )
        response = client.post("/api/v1/alerts/test", json={"channel": "webhook"})

        assert response.status_code == 502
