"""`/api/v1/reports/daily`: the cached daily brief, a background refresh,
and an on-demand Telegram send.

`GET /daily` only reads the cache the scheduler's 08:00 UK job (or
`POST /daily/refresh`) writes, so it never runs a universe scan inline.

Sending never returns a 500 for a Telegram failure: a refused or
unreachable send comes back as `sent=false` with the error, with the bot
token scrubbed from it (requests puts the full URL in its messages).
"""

from __future__ import annotations

import logging

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from ..alerts.config_store import effective_channel_config
from ..alerts.dispatcher import TelegramAlertChannel
from ..config import Settings, get_settings
from ..reports import daily
from .schemas import (
    DailyBriefRefreshResponse,
    DailyBriefResponse,
    DailyBriefSendResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


@router.get("/daily", response_model=DailyBriefResponse)
def get_daily_brief(settings: Settings = Depends(get_settings)) -> dict:
    """The most recently cached brief. Nothing is built or sent."""
    cached = daily.load_cached_brief(settings)
    if cached is None:
        raise HTTPException(
            status_code=404,
            detail="No daily brief yet: it is built at 08:00 UK on weekdays, "
            "or POST /api/v1/reports/daily/refresh to build one now.",
        )
    return cached


@router.post(
    "/daily/refresh", response_model=DailyBriefRefreshResponse, status_code=202
)
def refresh_daily_brief(
    background_tasks: BackgroundTasks, settings: Settings = Depends(get_settings)
) -> dict:
    """Rebuild the cached brief after the response is sent. Nothing is sent
    to Telegram; poll `GET /daily` and compare `computed_at`."""
    if daily.BRIEF_LOCK.locked():
        return {"status": "already_running"}
    background_tasks.add_task(daily.refresh_cached_brief, settings)
    return {"status": "started"}


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
