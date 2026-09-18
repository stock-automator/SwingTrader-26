"""Multichannel setup alerts (Telegram, Discord, generic webhook)."""

from .dispatcher import (
    AlertDispatcher,
    AlertMessage,
    DiscordAlertChannel,
    TelegramAlertChannel,
    WebhookAlertChannel,
)

__all__ = [
    "AlertDispatcher",
    "AlertMessage",
    "DiscordAlertChannel",
    "TelegramAlertChannel",
    "WebhookAlertChannel",
]
