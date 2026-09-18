"""
Multichannel setup alerts: Telegram, Discord, and a generic HTTP webhook.

Each channel is a thin, independent HTTP POST wrapper that raises on
failure - network error, timeout, or a non-2xx response. `AlertDispatcher`
is what makes that safe to call in bulk: it isolates each channel's failure
so a misconfigured Discord webhook can't take down a working Telegram
channel in the same `dispatch()` call.

This module never decides *when* to alert - it's on-demand infrastructure
wired to `POST /api/v1/alerts/dispatch`. Nothing here auto-fires from the
screener or signal-matrix scans; that would turn every poll into a
notification storm the moment a channel is configured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import requests

from ..config import Settings

log = logging.getLogger(__name__)

#: Every HTTP call in this module is short-lived (a single POST to a
#: notification endpoint) - a stuck alert channel must not be able to hang
#: a request thread indefinitely.
REQUEST_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class AlertMessage:
    """A single alert, channel-agnostic - each channel formats it its own way."""

    title: str
    body: str
    ticker: str | None = None
    direction: str | None = None
    url: str | None = None


class TelegramAlertChannel:
    """Posts to the Telegram Bot API's `sendMessage` endpoint."""

    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send(self, message: AlertMessage) -> None:
        """Raises `requests.RequestException` on a network failure, or
        `requests.HTTPError` on a non-2xx response."""
        lines = [f"*{message.title}*", message.body]
        if message.ticker:
            lines.insert(1, f"Ticker: {message.ticker}")
        if message.direction:
            lines.insert(2, f"Direction: {message.direction}")
        if message.url:
            lines.append(message.url)

        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": "\n".join(lines),
                "parse_mode": "Markdown",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()


class DiscordAlertChannel:
    """Posts a rich embed to a Discord incoming webhook."""

    name = "discord"

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, message: AlertMessage) -> None:
        """Raises `requests.RequestException` on a network failure, or
        `requests.HTTPError` on a non-2xx response."""
        fields = []
        if message.ticker:
            fields.append({"name": "Ticker", "value": message.ticker, "inline": True})
        if message.direction:
            fields.append(
                {"name": "Direction", "value": message.direction, "inline": True}
            )

        embed = {"title": message.title, "description": message.body, "fields": fields}
        if message.url:
            embed["url"] = message.url

        response = requests.post(
            self.webhook_url,
            json={"embeds": [embed]},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()


class WebhookAlertChannel:
    """Posts the message as a flat JSON dict to an arbitrary HTTP endpoint."""

    name = "webhook"

    def __init__(self, url: str):
        self.url = url

    def send(self, message: AlertMessage) -> None:
        """Raises `requests.RequestException` on a network failure, or
        `requests.HTTPError` on a non-2xx response."""
        response = requests.post(
            self.url,
            json={
                "title": message.title,
                "body": message.body,
                "ticker": message.ticker,
                "direction": message.direction,
                "url": message.url,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()


class AlertChannel(Protocol):
    """Structural type every channel above satisfies - `name` plus `send`."""

    name: str

    def send(self, message: AlertMessage) -> None: ...


class AlertDispatcher:
    """Fans one `AlertMessage` out to every configured channel, independently.

    Args:
        channels: Channels to dispatch to. An empty list is valid - it just
            means nothing is configured yet, not an error.
    """

    def __init__(self, channels: list[AlertChannel]):
        self.channels = channels

    @classmethod
    def from_settings(cls, settings: Settings) -> "AlertDispatcher":
        """Builds only the channels whose credentials are fully configured,
        from `settings`' environment-derived values alone. Routes that also
        honor the UI-editable config store (`alerts.config_store`) should
        call `from_channel_config` with `config_store.effective_channel_config`
        instead."""
        return cls.from_channel_config(
            {
                "telegram_bot_token": settings.telegram_bot_token,
                "telegram_chat_id": settings.telegram_chat_id,
                "discord_webhook_url": settings.discord_webhook_url,
                "generic_webhook_url": settings.generic_webhook_url,
            }
        )

    @classmethod
    def from_channel_config(cls, config: dict[str, str | None]) -> "AlertDispatcher":
        """Builds only the channels whose credentials are fully configured,
        from a plain `{field: value}` dict - the shape both `Settings` and
        `alerts.config_store.effective_channel_config` produce."""
        channels: list[AlertChannel] = []
        if config.get("telegram_bot_token") and config.get("telegram_chat_id"):
            channels.append(
                TelegramAlertChannel(
                    config["telegram_bot_token"], config["telegram_chat_id"]
                )
            )
        if config.get("discord_webhook_url"):
            channels.append(DiscordAlertChannel(config["discord_webhook_url"]))
        if config.get("generic_webhook_url"):
            channels.append(WebhookAlertChannel(config["generic_webhook_url"]))
        return cls(channels)

    def dispatch(self, message: AlertMessage) -> dict[str, str]:
        """Sends `message` to every configured channel.

        Returns:
            `{channel_name: "sent"}` or `{channel_name: "failed: <reason>"}`
            for each configured channel - never raises itself, since one
            channel's outage should not prevent reporting (or attempting)
            the others.
        """
        results: dict[str, str] = {}
        for channel in self.channels:
            try:
                channel.send(message)
                results[channel.name] = "sent"
            except requests.RequestException as exc:
                log.warning("alert channel %s failed: %s", channel.name, exc)
                results[channel.name] = f"failed: {exc}"
        return results
