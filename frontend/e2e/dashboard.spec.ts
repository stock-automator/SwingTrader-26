import { test, expect } from "@playwright/test";
import { API_BASE, mockCoreRoutes } from "./helpers";

const ROWS = [
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
    note: "Breakout above 20d high",
    win_probability: 0.62,
    win_probability_method: "regime_backtest",
    win_probability_sample_size: 40,
    win_probability_confidence_low: 0.5,
    win_probability_confidence_high: 0.74,
    win_probability_note: null,
  },
  {
    ticker: "TSLA",
    strategy: "vcp_breakout",
    direction: "SHORT",
    tradable: true,
    as_of: "2026-01-01",
    close: 180.0,
    entry_price: 180.0,
    stop_loss: 190.0,
    take_profit: 160.0,
    shares: 3,
    risk_amount: 30.0,
    reward_risk_ratio: 1.5,
    notional_value: 540.0,
    note: "VCP breakdown",
    win_probability: null,
    win_probability_method: null,
    win_probability_sample_size: null,
    win_probability_confidence_low: null,
    win_probability_confidence_high: null,
    win_probability_note: null,
  },
];

// Seven rows beyond the first give us 9 LONG/SHORT setups total, well past
// the dashboard's 6-setup portfolio-heat cap (6% account risk / 1%-per-setup)
// so the CAPPED_BY_PORTFOLIO_HEAT badge has something to flag.
const EXTRA_ROWS = Array.from({ length: 7 }, (_, i) => ({
  ticker: `SYM${i}`,
  strategy: "moving_average_cross",
  direction: "LONG" as const,
  tradable: true,
  as_of: "2026-01-01",
  close: 50.0,
  entry_price: 50.0,
  stop_loss: 45.0,
  take_profit: 60.0,
  shares: 10,
  risk_amount: 50.0,
  reward_risk_ratio: 2.0,
  notional_value: 500.0,
  note: null,
  win_probability: 0.4 - i * 0.01,
  win_probability_method: null,
  win_probability_sample_size: null,
  win_probability_confidence_low: null,
  win_probability_confidence_high: null,
  win_probability_note: null,
}));

test.beforeEach(async ({ page }) => {
  await mockCoreRoutes(page);
  await page.route(`${API_BASE}/api/v1/signals/live-today**`, (route) =>
    route.fulfill({
      json: {
        generated_at: "2026-01-01T00:00:00Z",
        rows: [...ROWS, ...EXTRA_ROWS],
        scanned: ROWS.length + EXTRA_ROWS.length,
        warnings: [],
      },
    }),
  );
});

test("dashboard shows the unified opportunity feed with heat-capped rows", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("nav-dashboard").click();

  await expect(page.getByTestId("dashboard-setup-row")).toHaveCount(
    ROWS.length + EXTRA_ROWS.length,
  );

  // Highest quality-score rows (AAPL win_probability=0.62, then the SYM*
  // rows) rank inside the top-6 cap; the lowest-ranked LONG/SHORT rows spill
  // over and get flagged.
  const cappedBadges = page.getByTestId("portfolio-heat-capped-badge");
  await expect(cappedBadges.first()).toBeVisible();
  await expect(cappedBadges.first()).toHaveText("CAPPED_BY_PORTFOLIO_HEAT");
});

test("starting a background scan pulses the scan status pill", async ({ page }) => {
  let jobRequested = false;
  await page.route(`${API_BASE}/api/v1/scans`, (route) => {
    if (route.request().method() === "POST") {
      jobRequested = true;
      return route.fulfill({
        status: 202,
        json: { job_id: "job-e2e-1", status: "pending" },
      });
    }
    return route.fulfill({ json: { jobs: [] } });
  });
  await page.route(`${API_BASE}/api/v1/scans/job-e2e-1`, (route) =>
    route.fulfill({
      json: { job_id: "job-e2e-1", status: "running", results: null, error: null },
    }),
  );

  await page.goto("/");
  await page.getByTestId("nav-dashboard").click();
  await page.getByTestId("scan-status-pill").click();

  await expect(page.getByTestId("scan-status-pill")).toContainText("Scanning");
  expect(jobRequested).toBe(true);
});

test("background scan survives navigating away to another tab", async ({ page }) => {
  await page.route(`${API_BASE}/api/v1/scans`, (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({
        status: 202,
        json: { job_id: "job-e2e-2", status: "pending" },
      });
    }
    return route.fulfill({ json: { jobs: [] } });
  });
  await page.route(`${API_BASE}/api/v1/scans/job-e2e-2`, (route) =>
    route.fulfill({
      json: { job_id: "job-e2e-2", status: "running", results: null, error: null },
    }),
  );

  await page.goto("/");
  await page.getByTestId("nav-dashboard").click();
  await page.getByTestId("scan-status-pill").click();
  await expect(page.getByTestId("scan-status-pill")).toContainText("Scanning");

  // Navigate to an unrelated tab and back - the scan indicator must not
  // reset, since ScanProvider/QueryClient live above the tab switch.
  await page.getByTestId("nav-journal").click();
  await page.getByTestId("nav-dashboard").click();

  await expect(page.getByTestId("scan-status-pill")).toContainText("Scanning");
});

test("clicking Trade opens the order ticket drawer for a setup", async ({ page }) => {
  await page.route(`${API_BASE}/api/v1/order-ticket`, (route) =>
    route.fulfill({
      json: {
        tickets: [
          {
            ticker: "AAPL",
            account_equity: 1000,
            order_type: "market",
            entry_price: 200.0,
            stop_loss: 190.0,
            take_profit: 220.0,
            quantity: 5,
            notional_value: 1000.0,
            risk_amount: 50.0,
            reward_risk_ratio: 2.0,
            tradable: true,
            note: null,
          },
        ],
        min_reward_risk_ratio: 1.5,
      },
    }),
  );

  await page.goto("/");
  await page.getByTestId("nav-dashboard").click();
  await page.getByTestId("dashboard-trade-button").first().click();

  // ScreenerGrid stays mounted off-tab (see App.tsx) and owns its own
  // OrderTicketDrawer instance, so two share this testid - scope to the one
  // that actually opened for the ticket we just requested.
  const drawer = page.getByTestId("order-ticket-drawer").filter({ hasText: "AAPL" });
  await expect(drawer).toBeVisible();
});
