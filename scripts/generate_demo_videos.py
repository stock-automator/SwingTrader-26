"""
Record one walkthrough video of every SwingTrader feature with Playwright.

Preconditions: backend on :8000 and the frontend dev server on :5173 are
already running (this script does not start them):

    uvicorn backend.app.main:app --port 8000
    cd frontend && npm run dev -- --host 127.0.0.1 --port 5173

Setup: pip install playwright requests && playwright install chromium

Usage: python3 scripts/generate_demo_videos.py

Output: docs/media/demo_all_features.webm (all 8 tabs, one backtest, then the
API docs and the daily/weekly/data-health JSON). It is read-only: it never
clicks order, cancel, close-all or alert-setting controls.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

BACKEND_URL = "http://127.0.0.1:8000"
FRONTEND_URL = "http://127.0.0.1:5173"
REPO_ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = REPO_ROOT / "docs" / "media"
OUT_NAME = "demo_all_features.webm"
PAUSE_MS = 2500
RENDER_TIMEOUT_MS = 150_000

TABS = [
    ("Dashboard", "nav-dashboard"),
    ("Live Screener", "nav-live-screener"),
    ("Signal Matrix", "nav-signal-matrix"),
    ("Backtesting Studio", "nav-backtest-studio"),
    ("Portfolio", "nav-portfolio"),
    ("Trade Journal", "nav-journal"),
    ("Alerts", "nav-alerts"),
    ("Historical Simulator", "nav-simulator"),
]
API_PAGES = [
    "/docs",
    "/api/v1/reports/daily",
    "/api/v1/reports/weekly",
    "/api/v1/data/health",
]


def check_servers_running() -> None:
    for name, url in (
        ("backend", f"{BACKEND_URL}/api/v1/health"),
        ("frontend", FRONTEND_URL),
    ):
        try:
            requests.get(url, timeout=5).raise_for_status()
        except requests.RequestException as exc:
            raise SystemExit(f"{name} not reachable at {url}: {exc}") from exc


def run_backtest(page) -> None:
    page.get_by_test_id("backtest-ticker-input").fill("AAPL")
    page.wait_for_timeout(1000)
    page.get_by_test_id("backtest-run-button").click()
    page.get_by_test_id("equity-chart").first.wait_for(
        state="visible", timeout=RENDER_TIMEOUT_MS
    )


def record() -> Path:
    tmp = MEDIA_DIR / "_tmp_demo"
    shutil.rmtree(tmp, ignore_errors=True)
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            record_video_dir=str(tmp),
            record_video_size={"width": 1600, "height": 1000},
        )
        page = context.new_page()
        page.goto(FRONTEND_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(PAUSE_MS)
        for label, testid in TABS:
            page.get_by_test_id(testid).click()
            page.wait_for_timeout(PAUSE_MS)
            if label == "Backtesting Studio":
                try:
                    run_backtest(page)
                except Exception as exc:  # keep recording the rest
                    errors.append(f"backtest: {exc}")
                page.wait_for_timeout(PAUSE_MS)
            if page.get_by_text("Something went wrong").count():
                errors.append(f"{label}: error boundary shown")
        for path in API_PAGES:
            try:
                page.goto(
                    f"{BACKEND_URL}{path}",
                    wait_until="load",
                    timeout=RENDER_TIMEOUT_MS,
                )
            except Exception as exc:  # keep recording the rest
                errors.append(f"{path}: {exc}")
            page.wait_for_timeout(PAUSE_MS)
        video = page.video
        page.close()
        context.close()
        dest = MEDIA_DIR / OUT_NAME
        shutil.move(str(video.path()), str(dest))
        browser.close()
    shutil.rmtree(tmp, ignore_errors=True)
    for err in errors:
        print(f"WARNING {err}")
    return dest


def main() -> int:
    check_servers_running()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    dest = record()
    print(f"Saved {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
