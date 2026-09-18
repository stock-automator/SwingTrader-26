import { test, expect } from "@playwright/test";

//: Foundation smoke test - the app loads and every top-level tab renders
//: without throwing, against a mocked backend (no live API dependency,
//: matching how the backend test suite never hits real network/broker
//: services). Not the full success/failure/network-degradation matrix the
//: product spec calls for - see the frontend README/handoff notes for what
//: remains.
const API_BASE = "http://localhost:8000";

test.beforeEach(async ({ page }) => {
  const pageErrors: Error[] = [];
  page.on("pageerror", (err) => pageErrors.push(err));
  (page as unknown as { _pageErrors: Error[] })._pageErrors = pageErrors;

  await page.route(`${API_BASE}/api/v1/health`, (route) =>
    route.fulfill({
      json: { status: "ok", finnhub_configured: false, allow_downloads: true },
    }),
  );
  await page.route(`${API_BASE}/api/v1/data/sync/status`, (route) =>
    route.fulfill({ json: { in_progress: false, started_at: null, results: [] } }),
  );
  await page.route(`${API_BASE}/api/v1/screener/live**`, (route) =>
    route.fulfill({
      json: {
        setups: [],
        scanned: 0,
        skipped: 0,
        skip_reasons: {},
        warnings: [],
        macro_regime: "UNKNOWN",
        circuit_breaker_active: false,
      },
    }),
  );
  await page.route(`${API_BASE}/api/v1/signals/live-today**`, (route) =>
    route.fulfill({
      json: {
        generated_at: "2026-01-01T00:00:00Z",
        rows: [
          {
            ticker: "AAPL",
            strategy: "donchian_breakout",
            direction: "LONG",
            tradable: true,
            as_of: "2026-01-01",
            close: 200.0,
            entry_price: 200.0,
            stop_loss: 190.0,
            take_profit: 220.0,
            shares: 5,
            risk_amount: 50.0,
            reward_risk_ratio: 2.0,
            notional_value: 1000.0,
            note: null,
            win_probability: null,
          },
        ],
        scanned: 1,
        warnings: [],
      },
    }),
  );
});

test("app loads and every top-level tab renders without throwing", async ({
  page,
}) => {
  // Every tab added since this test was written gets a 503 here - each
  // handles "backend not configured" as a normal empty state (see
  // portfolio.spec.ts / alerts.spec.ts / journal.spec.ts for their
  // success-path coverage), so this smoke test only has to prove that
  // switching to it doesn't throw.
  await page.route(`${API_BASE}/api/v1/execution/**`, (route) =>
    route.fulfill({ status: 503, json: { detail: "Alpaca is not configured" } }),
  );
  await page.route(`${API_BASE}/api/v1/alerts/config`, (route) =>
    route.fulfill({
      json: {
        telegram_bot_token: null,
        telegram_chat_id: null,
        discord_webhook_url: null,
        generic_webhook_url: null,
        telegram_configured: false,
        discord_configured: false,
        webhook_configured: false,
      },
    }),
  );
  await page.route(`${API_BASE}/api/v1/journal/summary`, (route) =>
    route.fulfill({ json: { error: "No completed trades yet" } }),
  );
  await page.route(`${API_BASE}/api/v1/journal/decay**`, (route) =>
    route.fulfill({ json: { windows: {} } }),
  );
  await page.route(`${API_BASE}/api/v1/journal/trades`, (route) =>
    route.fulfill({ json: { trades: [] } }),
  );
  await page.route(`${API_BASE}/api/v1/journal/mae-mfe-distribution`, (route) =>
    route.fulfill({ json: { points: [], warnings: [] } }),
  );

  await page.goto("/");
  await expect(page.getByText("SWINGTRADER")).toBeVisible();

  // Live Screener (default tab)
  await expect(page.getByTestId("nav-live-screener")).toBeVisible();

  // Signal Matrix
  await page.getByTestId("nav-signal-matrix").click();
  await expect(page.getByTestId("signal-matrix-row").first()).toBeVisible();
  await expect(page.getByTestId("signal-matrix-row")).toContainText("AAPL");

  // Backtesting Studio
  await page.getByTestId("nav-backtest-studio").click();
  await expect(page.getByTestId("backtest-run-button")).toBeVisible();

  // Portfolio
  await page.getByTestId("nav-portfolio").click();
  await expect(page.getByTestId("portfolio-not-configured")).toBeVisible();

  // Trade Journal
  await page.getByTestId("nav-journal").click();
  await expect(page.getByTestId("journal-trades-empty")).toBeVisible();

  // Alerts
  await page.getByTestId("nav-alerts").click();
  await expect(page.getByTestId("channel-card-telegram")).toBeVisible();

  // Back to Live Screener
  await page.getByTestId("nav-live-screener").click();
  await expect(page.getByTestId("nav-live-screener")).toBeVisible();

  const pageErrors = (page as unknown as { _pageErrors: Error[] })._pageErrors;
  expect(pageErrors).toEqual([]);
});
