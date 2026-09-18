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
        screener_max_tickers: Hard cap on tickers scanned per screener call.
            A full 500-name scan reads 500 parquet files and runs 500
            strategy passes; uncapped, one request can pin a worker for
            minutes.
        ws_poll_seconds: Interval between WebSocket screener pushes.
        max_backtest_tickers: Cap on tickers per backtest request, for the
            same reason.
    """

    finnhub_api_key: str | None = None
    data_dir: Path = Path("data/raw")
    allow_downloads: bool = True
    cors_origins: tuple[str, ...] = field(default=DEFAULT_CORS_ORIGINS)
    watchlist_path: Path = Path("config/watchlist.txt")
    screener_max_tickers: int = 60
    ws_poll_seconds: float = 15.0
    max_backtest_tickers: int = 10

    @property
    def has_finnhub(self) -> bool:
        return bool(self.finnhub_api_key)


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
        screener_max_tickers=_env_int("SCREENER_MAX_TICKERS", 60),
        ws_poll_seconds=_env_float("WS_POLL_SECONDS", 15.0),
        max_backtest_tickers=_env_int("MAX_BACKTEST_TICKERS", 10),
    )
