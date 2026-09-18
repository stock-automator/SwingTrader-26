"""
Runtime configuration, read from the environment once per process.

Nothing here has a secret as its default: an unset `FINNHUB_API_KEY` means
the live screener falls back to yfinance quotes rather than silently
running with a placeholder key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Loads a repo-root `.env` file into the process environment, if one exists,
# before `get_settings()` reads from it - lets a beginner following the
# README's "create a .env file" step just work, rather than needing to
# `export` each variable in their shell. A real environment variable already
# set always wins (`load_dotenv`'s default: it does not override existing
# keys), so this is a convenience layer, not a second source of truth.
load_dotenv()

#: Default browser origins allowed to call the API - the Vite dev server.
DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_optional_int(name: str, default: int | None) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Process-wide settings.

    Attributes:
        finnhub_api_key: Enables Finnhub real-time quotes on the live
            screener. Unset means yfinance-only.
        data_dir: Parquet cache directory.
        allow_downloads: Whether the API may hit a live data provider for an
            uncached ticker. Off in CI so a test run cannot depend on the
            network.
        cors_origins: Browser origins allowed to call the API.
        watchlist_path: Newline-delimited ticker list the screener scans.
        screener_max_tickers: Hard cap on tickers scanned per screener call -
            a safety valve against an accidentally enormous watchlist, not a
            throttle. Loading is parallelized across `screener_max_workers`
            threads (see `api.deps.load_frames`), so this can comfortably
            cover a full multi-hundred-name watchlist without pinning a
            worker for minutes; raise it further only if your watchlist
            outgrows the default.
        screener_max_workers: Thread-pool size `load_frames` uses to fetch
            tickers concurrently. Bounds how many parquet reads / yfinance
            requests are in flight at once - both the parallelism and the
            de facto rate limit against the data provider.
        screener_min_avg_volume: Tickers whose trailing average volume
            (see `screener_volume_lookback`) falls below this are skipped
            as `VOLUME_FILTER_FAILED` rather than scanned - an illiquid
            name's setup is not tradable at any real size.
        screener_volume_lookback: Trailing bar count `screener_min_avg_volume`
            is averaged over.
        screener_stale_after_days: Skip a ticker as `DATA_STALE` if its most
            recent bar is more than this many days old. `None` (the default)
            disables the check - a lagging local parquet sync should not
            silently empty the screener for every name in the watchlist.
        ws_poll_seconds: Interval between WebSocket screener pushes.
        max_backtest_tickers: Cap on tickers per backtest request, for the
            same reason.
        journal_path: CSV file `journal.TradeJournal` reads/writes trade
            history to.
        telegram_bot_token: Bot token for the Telegram alert channel. Unset
            (with `telegram_chat_id`) means Telegram alerts are disabled.
        telegram_chat_id: Destination chat id for Telegram alerts.
        discord_webhook_url: Discord incoming-webhook URL. Unset means
            Discord alerts are disabled.
        generic_webhook_url: Arbitrary HTTP endpoint alerts are POSTed to as
            flat JSON. Unset means the generic webhook channel is disabled.
        alpaca_api_key: Alpaca paper-trading API key. Unset means the
            execution endpoints return 503 rather than silently no-opping.
        alpaca_api_secret: Alpaca paper-trading API secret.
    """

    finnhub_api_key: str | None = None
    data_dir: Path = Path("data/raw")
    allow_downloads: bool = True
    cors_origins: tuple[str, ...] = field(default=DEFAULT_CORS_ORIGINS)
    watchlist_path: Path = Path("config/watchlist.txt")
    screener_max_tickers: int = 750
    screener_max_workers: int = 16
    screener_min_avg_volume: float = 100_000.0
    screener_volume_lookback: int = 20
    screener_stale_after_days: int | None = None
    ws_poll_seconds: float = 15.0
    max_backtest_tickers: int = 10
    journal_path: Path = Path("data/trades_live.csv")
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    discord_webhook_url: str | None = None
    generic_webhook_url: str | None = None
    alpaca_api_key: str | None = None
    alpaca_api_secret: str | None = None

    @property
    def has_finnhub(self) -> bool:
        return bool(self.finnhub_api_key)

    @property
    def has_alpaca(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_api_secret)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings. FastAPI depends on this, and tests override it via
    `app.dependency_overrides` rather than mutating the environment."""
    origins = os.environ.get("CORS_ORIGINS", "").strip()

    return Settings(
        finnhub_api_key=os.environ.get("FINNHUB_API_KEY") or None,
        data_dir=Path(os.environ.get("DATA_DIR", "data/raw")),
        allow_downloads=_env_bool("ALLOW_DOWNLOADS", True),
        cors_origins=(
            tuple(o.strip() for o in origins.split(",") if o.strip())
            if origins
            else DEFAULT_CORS_ORIGINS
        ),
        watchlist_path=Path(os.environ.get("WATCHLIST_PATH", "config/watchlist.txt")),
        screener_max_tickers=_env_int("SCREENER_MAX_TICKERS", 750),
        screener_max_workers=_env_int("SCREENER_MAX_WORKERS", 16),
        screener_min_avg_volume=_env_float("SCREENER_MIN_AVG_VOLUME", 100_000.0),
        screener_volume_lookback=_env_int("SCREENER_VOLUME_LOOKBACK", 20),
        screener_stale_after_days=_env_optional_int("SCREENER_STALE_AFTER_DAYS", None),
        ws_poll_seconds=_env_float("WS_POLL_SECONDS", 15.0),
        max_backtest_tickers=_env_int("MAX_BACKTEST_TICKERS", 10),
        journal_path=Path(os.environ.get("JOURNAL_PATH", "data/trades_live.csv")),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID") or None,
        discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL") or None,
        generic_webhook_url=os.environ.get("GENERIC_WEBHOOK_URL") or None,
        alpaca_api_key=os.environ.get("ALPACA_API_KEY") or None,
        alpaca_api_secret=os.environ.get("ALPACA_API_SECRET") or None,
    )
