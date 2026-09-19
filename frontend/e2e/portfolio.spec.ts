import { test, expect } from "@playwright/test";
import { API_BASE, mockCoreRoutes } from "./helpers";

const ACCOUNT = {
  account_number: "PA123",
  status: "ACTIVE",
  equity: 10500.25,
  cash: 4200.5,
  buying_power: 20000,
  portfolio_value: 10500.25,
};

const POSITIONS = {
  positions: [
    {
      symbol: "AAPL",
      side: "long",
      qty: 10,
      avg_entry_price: 190.0,
      current_price: 200.0,
      market_value: 2000.0,
      cost_basis: 1900.0,
      unrealized_pl: 100.0,
      unrealized_plpc: 0.0526,
    },
  ],
};

const HISTORY = {
  timestamp: ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"],
  equity: [10000, 10500.25],
  profit_loss: [0, 500.25],
  profit_loss_pct: [0, 0.05],
  base_value: 10000,
  timeframe: "1D",
};

test.beforeEach(async ({ page }) => {
  await mockCoreRoutes(page);
});

test("renders account stats, equity chart, and open positions", async ({ page }) => {
  await page.route(`${API_BASE}/api/v1/execution/account`, (route) =>
    route.fulfill({ json: ACCOUNT }),
  );
  await page.route(`${API_BASE}/api/v1/execution/positions`, (route) =>
    route.fulfill({ json: POSITIONS }),
  );
  await page.route(`${API_BASE}/api/v1/execution/portfolio-history**`, (route) =>
    route.fulfill({ json: HISTORY }),
  );

  await page.goto("/");
  await page.getByTestId("nav-portfolio").click();

  await expect(page.getByText("$10,500.25")).toBeVisible();
  await expect(page.getByTestId("positions-table")).toBeVisible();
  await expect(page.getByTestId("position-row")).toContainText("AAPL");
  await expect(page.getByTestId("portfolio-equity-chart")).toBeVisible();
});

test("shows a not-configured notice when Alpaca isn't set up", async ({ page }) => {
  await page.route(`${API_BASE}/api/v1/execution/account`, (route) =>
    route.fulfill({ status: 503, json: { detail: "Alpaca is not configured" } }),
  );
  await page.route(`${API_BASE}/api/v1/execution/positions`, (route) =>
    route.fulfill({ status: 503, json: { detail: "Alpaca is not configured" } }),
  );
  await page.route(`${API_BASE}/api/v1/execution/portfolio-history**`, (route) =>
    route.fulfill({ status: 503, json: { detail: "Alpaca is not configured" } }),
  );

  await page.goto("/");
  await page.getByTestId("nav-portfolio").click();

  await expect(page.getByTestId("portfolio-not-configured")).toBeVisible();
  await expect(page.getByTestId("close-all-button")).toBeDisabled();
});

test("close all requires an explicit modal confirmation", async ({ page }) => {
  await page.route(`${API_BASE}/api/v1/execution/account`, (route) =>
    route.fulfill({ json: ACCOUNT }),
  );
  await page.route(`${API_BASE}/api/v1/execution/positions`, (route) =>
    route.fulfill({ json: POSITIONS }),
  );
  await page.route(`${API_BASE}/api/v1/execution/portfolio-history**`, (route) =>
    route.fulfill({ json: HISTORY }),
  );
  let closeAllCalled = false;
  await page.route(`${API_BASE}/api/v1/execution/close-all`, (route) => {
    closeAllCalled = true;
    return route.fulfill({
      json: { closed: [{ symbol: "AAPL", status: 200, order_id: "c-1" }] },
    });
  });

  await page.goto("/");
  await page.getByTestId("nav-portfolio").click();
  await expect(page.getByTestId("positions-table")).toBeVisible();

  await page.getByTestId("close-all-button").click();
  await expect(page.getByTestId("close-all-modal")).toBeVisible();

  // Cancel must not dispatch the request.
  await page.getByTestId("close-all-cancel").click();
  await expect(page.getByTestId("close-all-modal")).not.toBeVisible();
  expect(closeAllCalled).toBe(false);

  // Re-open and confirm actually dispatches it.
  await page.getByTestId("close-all-button").click();
  await page.getByTestId("close-all-confirm").click();
  await expect(page.getByText("Closed 1 position(s)")).toBeVisible();
  expect(closeAllCalled).toBe(true);
});
