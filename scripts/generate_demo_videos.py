"""
Record short demo videos of the SwingTrader frontend using Playwright.

This script does NOT start the backend or frontend for you — orchestrating
`uvicorn` / `npm run dev` as subprocesses from inside a recording script is
fragile (port races, slow cold starts, zombie processes on failure) and is
out of scope here. Instead it does a quick reachability check against both
dev servers and fails fast with a clear message if either is down.

Precondition — start both dev servers yourself, in separate terminals,
before running this script:

    # terminal 1
    uvicorn backend.app.main:app --reload --port 8000

    # terminal 2
    cd frontend && npm run dev

Setup:

    pip install playwright requests
    playwright install chromium

Usage:

    python3 scripts/generate_demo_videos.py

Output:

    docs/media/screener_walkthrough.webm
    docs/media/backtest_walkthrough.webm

Playwright's `record_video_dir` writes one `.webm` per browser context, with
a name Playwright itself chooses (a hash, not something this script
controls at creation time) — so each walkthrough runs in its own context/
temp directory, and the resulting file is renamed to the descriptive name
above once the context closes and the recording is flushed to disk.

Selector note: the frontend (`frontend/src/App.tsx`) is being built in
parallel and did not yet expose the Live Screener / Backtesting Studio views
described below at the time this script was written. Selectors are written
against `data-testid` attributes and visible label text as the *expected*
convention for those views; if the actual markup differs, update the
`SELECTORS` dict below rather than the walkthrough logic.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

try:
    import requests
except ImportError as exc:  # pragma: no cover - exercised by hand
    raise SystemExit(
        "This script requires the 'requests' package: pip install requests"
    ) from exc

try:
    from playwright.sync_api import Page, sync_playwright
except ImportError as exc:  # pragma: no cover - exercised by hand
    raise SystemExit(
        "This script requires Playwright: pip install playwright && "
        "playwright install chromium"
    ) from exc

BACKEND_URL = "http://localhost:8000"
FRONTEND_URL = "http://localhost:5173"
HEALTH_ENDPOINT = f"{BACKEND_URL}/api/v1/health"

REPO_ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = REPO_ROOT / "docs" / "media"

#: Expected selectors on the frontend. Update these, not the walkthrough
#: functions below, if the real markup differs once the UI lands.
SELECTORS = {
    "nav_screener": '[data-testid="nav-live-screener"], text="Live Screener"',
    "nav_backtest": '[data-testid="nav-backtest-studio"], text="Backtesting Studio"',
    "setup_row": '[data-testid="setup-row"]',
    "strategy_select": '[data-testid="backtest-strategy-select"]',
    "ticker_input": '[data-testid="backtest-ticker-input"]',
    "start_date_input": '[data-testid="backtest-start-date"]',
    "end_date_input": '[data-testid="backtest-end-date"]',
    "initial_capital_input": '[data-testid="backtest-initial-capital"]',
    "run_button": '[data-testid="backtest-run-button"], button:has-text("Run")',
    "equity_chart": '[data-testid="equity-chart"]',
}

#: How long to wait for dynamic content (setups, chart render) before giving
#: up, in milliseconds.
RENDER_TIMEOUT_MS = 20_000


def check_servers_running() -> None:
    """Fail fast with a clear message if either dev server is unreachable."""
    for name, url in (("backend", HEALTH_ENDPOINT), ("frontend", FRONTEND_URL)):
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SystemExit(
                f"{name} dev server is not reachable at {url} ({exc}).\n"
                "Start both dev servers before running this script:\n"
                "  uvicorn backend.app.main:app --reload --port 8000\n"
                "  cd frontend && npm run dev"
            ) from exc
    print(f"Backend OK ({HEALTH_ENDPOINT}), frontend OK ({FRONTEND_URL}).")


def _finalize_recording(page: Page, dest_name: str) -> None:
    """Close the page's context so Playwright flushes its video to disk,
    then move that video to `docs/media/<dest_name>`."""
    video = page.video
    context = page.context
    page.close()
    context.close()

    if video is None:
        print(f"WARNING: no video was recorded for {dest_name}")
        return

    recorded_path = Path(video.path())
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    dest = MEDIA_DIR / dest_name
    shutil.move(str(recorded_path), str(dest))
    print(f"Saved {dest}")


def record_screener_walkthrough(browser) -> None:
    """Load the Live Screener view and wait for setups to render."""
    context = browser.new_context(
        record_video_dir=str(MEDIA_DIR / "_tmp_screener"),
        record_video_size={"width": 1600, "height": 1000},
    )
    page = context.new_page()

    page.goto(FRONTEND_URL, wait_until="networkidle")
    page.locator(SELECTORS["nav_screener"]).first.click()

    # Wait for at least one setup row to render (the WebSocket pushes on
    # connect, so this should resolve quickly once the backend responds).
    page.locator(SELECTORS["setup_row"]).first.wait_for(
        state="visible", timeout=RENDER_TIMEOUT_MS
    )
    page.wait_for_timeout(2000)  # let a couple of rows settle in frame

    _finalize_recording(page, "screener_walkthrough.webm")


def record_backtest_walkthrough(browser) -> None:
    """Switch to the Backtesting Studio, fill the form, run a backtest, and
    wait for the equity chart to render."""
    context = browser.new_context(
        record_video_dir=str(MEDIA_DIR / "_tmp_backtest"),
        record_video_size={"width": 1600, "height": 1000},
    )
    page = context.new_page()

    page.goto(FRONTEND_URL, wait_until="networkidle")
    page.locator(SELECTORS["nav_backtest"]).first.click()

    page.locator(SELECTORS["strategy_select"]).first.select_option(
        "donchian_breakout"
    )
    page.locator(SELECTORS["ticker_input"]).first.fill("AAPL")
    page.locator(SELECTORS["start_date_input"]).first.fill("2022-01-01")
    page.locator(SELECTORS["end_date_input"]).first.fill("2024-01-01")
    page.locator(SELECTORS["initial_capital_input"]).first.fill("1000")

    page.locator(SELECTORS["run_button"]).first.click()

    # The backtest call can take a few seconds (full-history replay), so
    # give the equity chart a generous timeout to appear.
    page.locator(SELECTORS["equity_chart"]).first.wait_for(
        state="visible", timeout=RENDER_TIMEOUT_MS
    )
    page.wait_for_timeout(2000)  # let the chart finish drawing

    _finalize_recording(page, "backtest_walkthrough.webm")


def main() -> None:
    check_servers_running()
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            record_screener_walkthrough(browser)
            record_backtest_walkthrough(browser)
        finally:
            browser.close()

    # Clean up the temp per-context video directories Playwright wrote into.
    for tmp_dir in (MEDIA_DIR / "_tmp_screener", MEDIA_DIR / "_tmp_backtest"):
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("Done. Recordings saved under docs/media/.")


if __name__ == "__main__":
    main()
