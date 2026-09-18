"""Tests for `backend.app.alerts.dispatcher` - never makes a real HTTP call."""

import pytest
import requests

from backend.app.alerts.dispatcher import (
    AlertDispatcher,
    AlertMessage,
    DiscordAlertChannel,
    TelegramAlertChannel,
    WebhookAlertChannel,
)
from backend.app.config import Settings


class _FakeResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def _message() -> AlertMessage:
    return AlertMessage(
        title="LONG setup: AAPL",
        body="Donchian breakout at 190.00",
        ticker="AAPL",
        direction="LONG",
        url="https://example.com/AAPL",
    )


class TestTelegramAlertChannel:
    def test_send_posts_to_bot_endpoint(self, monkeypatch):
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["timeout"] = timeout
            return _FakeResponse(200)

        monkeypatch.setattr(requests, "post", fake_post)
        TelegramAlertChannel("TOKEN", "123").send(_message())

        assert captured["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
        assert captured["json"]["chat_id"] == "123"
        assert "AAPL" in captured["json"]["text"]

    def test_raises_on_non_2xx(self, monkeypatch):
        monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(401))
        with pytest.raises(requests.HTTPError):
            TelegramAlertChannel("TOKEN", "123").send(_message())


class TestDiscordAlertChannel:
    def test_send_posts_an_embed(self, monkeypatch):
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeResponse(204)

        monkeypatch.setattr(requests, "post", fake_post)
        DiscordAlertChannel("https://discord.example/webhook").send(_message())

        assert captured["url"] == "https://discord.example/webhook"
        embed = captured["json"]["embeds"][0]
        assert embed["title"] == "LONG setup: AAPL"
        assert any(f["value"] == "AAPL" for f in embed["fields"])


class TestWebhookAlertChannel:
    def test_send_posts_flat_json(self, monkeypatch):
        captured = {}

        def fake_post(url, json=None, timeout=None):
            captured["json"] = json
            return _FakeResponse(200)

        monkeypatch.setattr(requests, "post", fake_post)
        WebhookAlertChannel("https://example.com/hook").send(_message())

        assert captured["json"] == {
            "title": "LONG setup: AAPL",
            "body": "Donchian breakout at 190.00",
            "ticker": "AAPL",
            "direction": "LONG",
            "url": "https://example.com/AAPL",
        }


class TestAlertDispatcherFromSettings:
    def test_no_channels_configured_is_empty_not_an_error(self):
        dispatcher = AlertDispatcher.from_settings(Settings())
        assert dispatcher.channels == []
        assert dispatcher.dispatch(_message()) == {}

    def test_builds_only_fully_configured_channels(self):
        settings = Settings(
            telegram_bot_token="TOKEN",
            telegram_chat_id=None,  # incomplete - telegram must not activate
            discord_webhook_url="https://discord.example/webhook",
        )
        dispatcher = AlertDispatcher.from_settings(settings)
        names = {c.name for c in dispatcher.channels}
        assert names == {"discord"}

    def test_all_three_channels_activate_when_fully_configured(self):
        settings = Settings(
            telegram_bot_token="TOKEN",
            telegram_chat_id="123",
            discord_webhook_url="https://discord.example/webhook",
            generic_webhook_url="https://example.com/hook",
        )
        dispatcher = AlertDispatcher.from_settings(settings)
        names = {c.name for c in dispatcher.channels}
        assert names == {"telegram", "discord", "webhook"}


class TestAlertDispatcherIsolatesFailures:
    def test_one_failing_channel_does_not_block_another(self, monkeypatch):
        class _AlwaysSucceeds:
            name = "ok_channel"

            def send(self, message):
                pass

        class _AlwaysFails:
            name = "broken_channel"

            def send(self, message):
                raise requests.ConnectionError("boom")

        dispatcher = AlertDispatcher([_AlwaysFails(), _AlwaysSucceeds()])
        results = dispatcher.dispatch(_message())

        assert results["ok_channel"] == "sent"
        assert results["broken_channel"].startswith("failed:")
