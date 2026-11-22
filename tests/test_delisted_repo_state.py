"""
Cycle 01 (C-2 / C-4) repo-state checks: the tracked watchlist and data
directories reflect the Gate 1 decisions.

These read real repository files (read-only; nothing is ever written to
`data/`), so they verify the *data commit*, not code behaviour.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCHLIST = REPO_ROOT / "config" / "watchlist.txt"
RAW_DIR = REPO_ROOT / "data" / "raw"
DELISTED_DIR = REPO_ROOT / "data" / "delisted"

DENIED = ("EA", "KVUE", "WBD", "AVB", "EQR", "BK", "MMC", "CTRA", "K", "WBA")
NEW_LIVE = ("VMRK", "BNY", "MRSH")
ARCHIVES = {
    "KVUE_20261030.parquet": date(2026, 10, 30),
    "WBD_20261005.parquet": date(2026, 10, 5),
}


def _watchlist() -> set[str]:
    tickers = set()
    for line in WATCHLIST.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            tickers.add(line.upper())
    return tickers


def _last_day(path: Path) -> date:
    index = pd.read_parquet(path).index
    last = pd.Timestamp(index.max())
    if last.tzinfo is not None:
        last = last.tz_localize(None)
    return last.date()


@pytest.mark.parametrize("ticker", DENIED)
def test_c2_c4_watchlist_excludes_denied(ticker: str) -> None:
    assert ticker not in _watchlist()


@pytest.mark.parametrize("ticker", NEW_LIVE)
def test_c2_c4_watchlist_includes_successor(ticker: str) -> None:
    assert ticker in _watchlist()


@pytest.mark.parametrize("ticker", DENIED)
def test_c2_c4_no_raw_parquet_for_denied(ticker: str) -> None:
    assert not (RAW_DIR / f"{ticker}.parquet").exists()


@pytest.mark.parametrize("name", sorted(ARCHIVES))
def test_c2_archive_exists_trimmed_to_last_trading_day(name: str) -> None:
    path = DELISTED_DIR / name
    assert path.exists(), f"missing archive {path}"
    assert len(pd.read_parquet(path)) > 0
    assert _last_day(path) == ARCHIVES[name]


def test_c2_archive_dir_holds_only_agreed_files() -> None:
    assert DELISTED_DIR.is_dir()
    assert sorted(p.name for p in DELISTED_DIR.glob("*.parquet")) == sorted(ARCHIVES)


@pytest.mark.parametrize("ticker", NEW_LIVE)
def test_c2_c4_successor_parquet_exists_with_history(ticker: str) -> None:
    path = RAW_DIR / f"{ticker}.parquet"
    assert path.exists(), f"missing {path}"
    assert len(pd.read_parquet(path)) > 250, "expected at least ~1y of history"
