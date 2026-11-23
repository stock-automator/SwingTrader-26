"""`/api/v1/reports/daily`: preview or send the daily Telegram brief.

Sending never returns a 500 for a Telegram failure: a refused or
unreachable send comes back as `sent=false` with the error, with the bot
token scrubbed from it (requests puts the full URL in its messages).
"""

from __future__ import annotations

import dataclasses
import logging

import requests
from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from ..alerts.config_store import effective_channel_config
from ..alerts.dispatcher import TelegramAlertChannel
from ..config import Settings, get_settings
from ..reports import daily
from .schemas import DailyBriefResponse, DailyBriefSendResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


def _payload(brief: daily.DailyBrief) -> dict:
    body = dataclasses.asdict(brief)
    body["as_of"] = brief.as_of.isoformat() if brief.as_of else None
    body["last_session"] = brief.last_session.isoformat()
    return body


@router.get("/daily", response_model=DailyBriefResponse)
async def get_daily_brief(settings: Settings = Depends(get_settings)) -> dict:
    """Today's brief, built from the cached prices. Nothing is sent."""
    return _payload(await run_in_threadpool(daily.build_daily_brief, settings))


@router.post("/daily/send", response_model=DailyBriefSendResponse)
async def send_daily_brief(settings: Settings = Depends(get_settings)) -> dict:
    """Build today's brief and send it to the configured Telegram chat."""
    config = effective_channel_config(settings)
    token, chat_id = config["telegram_bot_token"], config["telegram_chat_id"]
    if not (token and chat_id):
        raise HTTPException(
            status_code=503,
            detail="Telegram is not configured: set TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID in your local .env.",
        )
    brief = await run_in_threadpool(daily.build_daily_brief, settings)
    channel = TelegramAlertChannel(token, chat_id)
    try:
        parts = await run_in_threadpool(channel.send_text, brief.text)
    except requests.RequestException as exc:
        error = str(exc).replace(token, "<token>")
        log.warning("daily brief send failed: %s", error)
        parts_sent = getattr(exc, "parts_sent", 0)
        return {"sent": False, "parts": parts_sent, "error": error}
    return {"sent": True, "parts": parts, "error": None}
