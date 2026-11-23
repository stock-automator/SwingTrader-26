"""In-process scheduled jobs: the 07:30 UK data sync and the 08:00 UK brief.

Both run Monday to Friday on Europe/London time (APScheduler follows UK
clock changes). `coalesce=True` with a 6-hour `misfire_grace_time` means a
Mac that slept through 08:00 runs each missed job once when it wakes, not
once per missed day. The jobs share the process scheduler the universe
refresh already uses (`data.universe._scheduler`), and only start when
`Settings.scheduler_enabled` (env `SCHEDULER_ENABLED`, default true).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apscheduler.triggers.cron import CronTrigger

from .alerts.config_store import effective_channel_config
from .alerts.dispatcher import TelegramAlertChannel
from .api.data_sync import run_watchlist_sync
from .config import Settings
from .data import universe
from .reports import daily

log = logging.getLogger(__name__)

TIMEZONE = "Europe/London"
WEEKDAYS = "mon-fri"
#: How late a missed run may still fire (e.g. after the Mac wakes).
MISFIRE_GRACE_SECONDS = 6 * 3600


@dataclass(frozen=True)
class JobSpec:
    id: str
    func: Callable[[], Any]
    trigger: CronTrigger
    coalesce: bool = True
    misfire_grace_time: int = MISFIRE_GRACE_SECONDS


def _weekdays_at(hour: int, minute: int) -> CronTrigger:
    return CronTrigger(
        day_of_week=WEEKDAYS, hour=hour, minute=minute, timezone=TIMEZONE
    )


def send_daily_brief_job(settings: Settings) -> None:
    """Build and cache today's brief, then send it to Telegram if a bot is
    configured. A send failure is logged (token scrubbed), never raised."""
    brief = daily.refresh_cached_brief(settings)
    config = effective_channel_config(settings)
    token, chat_id = config["telegram_bot_token"], config["telegram_chat_id"]
    if not (token and chat_id):
        log.info("daily brief cached; Telegram not configured, not sent")
        return
    try:
        parts = TelegramAlertChannel(token, chat_id).send_text(brief.text)
    except Exception as exc:  # noqa: BLE001 - a failed send must not kill the job
        log.warning("daily brief send failed: %s", str(exc).replace(token, "<token>"))
        return
    log.info("daily brief sent in %d part(s)", parts)


def build_jobs(settings: Settings) -> list[JobSpec]:
    return [
        JobSpec(
            id="data_sync",
            func=lambda: run_watchlist_sync(settings),
            trigger=_weekdays_at(7, 30),
        ),
        JobSpec(
            id="daily_brief",
            func=lambda: send_daily_brief_job(settings),
            trigger=_weekdays_at(8, 0),
        ),
    ]


def start_scheduler(app: Any, settings: Settings) -> Any:
    """Register the jobs on the shared process scheduler, starting it if
    needed. Returns the scheduler, or `None` when scheduling is disabled."""
    if not settings.scheduler_enabled:
        return None
    if universe._scheduler is None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        universe._scheduler = AsyncIOScheduler()
        universe._scheduler.start()
    for job in build_jobs(settings):
        universe._scheduler.add_job(
            job.func,
            trigger=job.trigger,
            id=job.id,
            coalesce=job.coalesce,
            misfire_grace_time=job.misfire_grace_time,
            replace_existing=True,
        )
    app.state.scheduler = universe._scheduler
    return universe._scheduler
