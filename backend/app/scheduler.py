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
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

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


#: One sync at a time; the brief job waits on this lock for a running sync.
_SYNC_LOCK = threading.Lock()
#: `data_dir` -> the UK date of its last completed sync.
_SYNCED: dict[str, date] = {}


def _uk_today(now: datetime | None = None) -> date:
    now = now or datetime.now(ZoneInfo(TIMEZONE))
    return now.astimezone(ZoneInfo(TIMEZONE)).date()


def _sync(settings: Settings, only_if_needed: bool = False) -> None:
    """Sync the watchlist; with `only_if_needed`, skip when today's sync for
    this `data_dir` already completed. A failure is logged, not raised."""
    key = str(settings.data_dir)
    with _SYNC_LOCK:
        today = _uk_today()
        if only_if_needed and _SYNCED.get(key) == today:
            return
        try:
            run_watchlist_sync(settings)
        except Exception as exc:  # noqa: BLE001 - the brief still builds
            log.warning("data sync failed: %s", type(exc).__name__)
            return
        _SYNCED[key] = today


def _guarded(name: str, fn: Callable[[], Any]) -> Callable[[], None]:
    """Log a failing job (exception type only, no secrets) instead of raising."""

    def run() -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log.error("scheduled job %s failed: %s", name, type(exc).__name__)

    return run


def _brief_after_sync(settings: Settings) -> None:
    _sync(settings, only_if_needed=True)
    send_daily_brief_job(settings)


def _brief_cached_today(settings: Settings, now: datetime) -> bool:
    cached = daily.load_cached_brief(settings)
    try:
        stamp = datetime.fromisoformat(cached["computed_at"])  # type: ignore[index]
    except (TypeError, KeyError, ValueError):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ZoneInfo("UTC"))
    return _uk_today(stamp) == _uk_today(now)


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
            func=_guarded("data_sync", lambda: _sync(settings)),
            trigger=_weekdays_at(7, 30),
        ),
        JobSpec(
            id="daily_brief",
            func=_guarded("daily_brief", lambda: _brief_after_sync(settings)),
            trigger=_weekdays_at(8, 0),
        ),
    ]


def start_scheduler(app: Any, settings: Settings, now: datetime | None = None) -> Any:
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
    uk_now = (now or datetime.now(ZoneInfo(TIMEZONE))).astimezone(ZoneInfo(TIMEZONE))
    if (
        uk_now.weekday() < 5
        and uk_now.hour >= 8
        and not _brief_cached_today(settings, uk_now)
    ):
        universe._scheduler.add_job(
            _guarded("brief_catch_up", lambda: _brief_after_sync(settings)),
            id="brief_catch_up",
            replace_existing=True,
        )
    app.state.scheduler = universe._scheduler
    return universe._scheduler
