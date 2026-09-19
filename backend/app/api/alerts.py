"""`/api/v1/alerts/*` - on-demand multichannel setup alerts, plus the
Alert Channel Configuration UI's backing store.

`POST /dispatch` and `POST /test` are dispatch-only - nothing here
auto-fires from a screener or signal-matrix scan. See `alerts/dispatcher.py`'s
module docstring for why.

Every route here resolves credentials through
`alerts.config_store.effective_channel_config`, which overlays whatever the
configuration UI has saved to disk on top of `Settings`' environment-derived
defaults - a channel configured only through the UI dispatches exactly like
one configured only via `.env`.
"""

from __future__ import annotations

import requests
from fastapi import APIRouter, Depends, HTTPException

from ..alerts.config_store import effective_channel_config, save_alert_config
from ..alerts.dispatcher import AlertDispatcher, AlertMessage
from ..config import Settings, get_settings
from .schemas import (
    AlertChannelsConfigRequest,
    AlertChannelsConfigResponse,
    AlertDispatchRequest,
    AlertDispatchResponse,
    AlertTestRequest,
    AlertTestResponse,
)

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

_TEST_MESSAGE = AlertMessage(
    title="SwingTrader Test Alert",
    body="This is a test alert from your SwingTrader Alert Channel settings. "
    "If you can read this, the channel is wired up correctly.",
)


@router.post("/dispatch", response_model=AlertDispatchResponse)
def dispatch_alert(
    request: AlertDispatchRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Sends one alert to every configured channel (Telegram/Discord/generic
    webhook). Returns `{"results": {}}` if none are configured - that's a
    valid, non-error state."""
    dispatcher = AlertDispatcher.from_channel_config(effective_channel_config(settings))
    message = AlertMessage(
        title=request.title,
        body=request.body,
        ticker=request.ticker,
        direction=request.direction,
        url=request.url,
    )
    return {"results": dispatcher.dispatch(message)}


@router.get("/config", response_model=AlertChannelsConfigResponse)
def get_alert_config(settings: Settings = Depends(get_settings)) -> dict:
    """Current effective channel config (stored file overlaid on env vars),
    plus per-channel `*_configured` flags the settings UI uses to render
    each channel's connected/disconnected state."""
    config = effective_channel_config(settings)
    return {
        **config,
        "telegram_configured": bool(
            config["telegram_bot_token"] and config["telegram_chat_id"]
        ),
        "discord_configured": bool(config["discord_webhook_url"]),
        "webhook_configured": bool(config["generic_webhook_url"]),
    }


@router.put("/config", response_model=AlertChannelsConfigResponse)
def put_alert_config(
    request: AlertChannelsConfigRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Persists the four channel fields to `settings.alert_config_path` -
    the only way to configure Telegram/Discord/webhook alerts without
    editing a backend `.env` file. Replaces the whole stored config; the UI
    always submits the full form."""
    save_alert_config(settings.alert_config_path, request.model_dump())
    return get_alert_config(settings)


@router.post("/test", response_model=AlertTestResponse)
def send_test_alert(
    request: AlertTestRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Sends a canned test message to exactly one channel, so the UI's "Send
    Test Alert" button can confirm a channel actually works before relying
    on it. 422 if that channel has no credentials configured (file or env)."""
    dispatcher = AlertDispatcher.from_channel_config(effective_channel_config(settings))
    channel = next((c for c in dispatcher.channels if c.name == request.channel), None)
    if channel is None:
        raise HTTPException(
            status_code=422,
            detail=f"{request.channel} is not configured - save its credentials "
            "first.",
        )

    try:
        channel.send(_TEST_MESSAGE)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502, detail=f"Test alert failed: {exc}"
        ) from exc

    return {"channel": request.channel, "status": "sent"}
