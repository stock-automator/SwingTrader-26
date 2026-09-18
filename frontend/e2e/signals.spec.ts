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
    note: null,
    win_probability: null,
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
    note: null,
    win_probability: null,
  },
];

test.beforeEach(async ({ page }) => {
  await mockCoreRoutes(page);
  await page.route(`${API_BASE}/api/v1/signals/live-today**`, (route) =>
    route.fulfill({
      json: { generated_at: "2026-01-01T00:00:00Z", rows: ROWS, scanned: 2, warnings: [] },
    }),
  );
});

test("ticker and direction filters narrow the signal matrix", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("nav-signal-matrix").click();

  await expect(page.getByTestId("signal-matrix-row")).toHaveCount(2);

  await page.getByLabel("Ticker").fill("AAPL");
  await expect(page.getByTestId("signal-matrix-row")).toHaveCount(1);
  await expect(page.getByTestId("signal-matrix-row")).toContainText("AAPL");

  await page.getByLabel("Ticker").fill("");
  await expect(page.getByTestId("signal-matrix-row")).toHaveCount(2);

  // Direction filter starts with LONG+SHORT both active - turning SHORT off
  // should leave only the LONG (AAPL) row.
  await page.getByRole("button", { name: "SHORT", exact: true }).click();
  await expect(page.getByTestId("signal-matrix-row")).toHaveCount(1);
  await expect(page.getByTestId("signal-matrix-row")).toContainText("AAPL");
});

test("submitting a paper trade shows a success toast", async ({ page }) => {
  await page.route(`${API_BASE}/api/v1/execution/orders`, (route) =>
    route.fulfill({
      json: {
        id: "order-1",
        symbol: "AAPL",
        qty: "5",
        side: "buy",
        type: "market",
        order_class: "simple",
        status: "accepted",
        submitted_at: null,
      },
    }),
  );

  await page.goto("/");
  await page.getByTestId("nav-signal-matrix").click();
  await page.getByTestId("paper-trade-button").first().click();

  await expect(page.getByText(/Paper order placed: AAPL/)).toBeVisible();
});

test("a 503 from the paper trade endpoint shows a configuration error toast", async ({
  page,
}) => {
  await page.route(`${API_BASE}/api/v1/execution/orders`, (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "Alpaca is not configured" },
    }),
  );

  await page.goto("/");
  await page.getByTestId("nav-signal-matrix").click();
  await page.getByTestId("paper-trade-button").first().click();

  await expect(page.getByText(/Alpaca isn't configured on this server/)).toBeVisible();
});
