"""Persisted alert-channel configuration, editable from the UI.

`config.Settings` seeds Telegram/Discord/webhook credentials from
environment variables at process start - fine for a deployment, but it
means changing a bot token requires editing `.env` and restarting the
process. This module is the alternative the Alert Channel Configuration UI
talks to: a small JSON file read fresh on every request (never cached), so
a save made through `PUT /api/v1/alerts/config` takes effect on the very
next dispatch or test call with no restart and no other backend file
touched.

A field saved here always wins over the same field's environment variable -
see `effective_channel_config`, the single place that merge happens.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import Settings

#: The only fields this store persists - one per alert channel. Order also
#: defines what `load_alert_config` will read back out of a legacy/foreign
#: JSON file; any other key present on disk is silently ignored.
CHANNEL_FIELDS = (
    "telegram_bot_token",
    "telegram_chat_id",
    "discord_webhook_url",
    "generic_webhook_url",
)


def load_alert_config(path: Path) -> dict[str, str | None]:
    """Reads the stored channel config, or `{}` if the file doesn't exist
    or isn't valid JSON - a missing/corrupt file means "nothing saved yet",
    not an error."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {field: raw.get(field) or None for field in CHANNEL_FIELDS if field in raw}


def save_alert_config(
    path: Path, config: dict[str, str | None]
) -> dict[str, str | None]:
    """Persists `config` (only recognized `CHANNEL_FIELDS`), replacing
    whatever was previously stored - the UI always submits the full form.
    Returns the config actually written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    to_write = {field: config.get(field) or None for field in CHANNEL_FIELDS}
    path.write_text(json.dumps(to_write, indent=2))
    return to_write


def effective_channel_config(settings: "Settings") -> dict[str, str | None]:
    """The config every dispatch/test call actually uses: the stored file
    overlaid on `settings`' environment-derived defaults, field by field."""
    stored = load_alert_config(settings.alert_config_path)
    return {
        "telegram_bot_token": stored.get("telegram_bot_token")
        or settings.telegram_bot_token,
        "telegram_chat_id": stored.get("telegram_chat_id") or settings.telegram_chat_id,
        "discord_webhook_url": stored.get("discord_webhook_url")
        or settings.discord_webhook_url,
        "generic_webhook_url": stored.get("generic_webhook_url")
        or settings.generic_webhook_url,
    }
