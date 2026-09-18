import { test, expect } from "@playwright/test";
import { API_BASE, mockCoreRoutes } from "./helpers";

const SUMMARY = {
  total_trades: 2,
  completed_trades: 2,
  open_trades: 0,
  win_rate: 0.5,
  profit_factor: 1.8,
  avg_winner: 200,
  avg_loser: -100,
  avg_pnl: 50,
  median_pnl: 50,
  avg_r_multiple: 1.2,
  max_consecutive_losses: 1,
  max_drawdown: -100,
  avg_holding_days: 3,
  error: null,
};

const DECAY = {
  windows: {
    "30": { trade_count: 2, win_rate: 0.5, avg_r_multiple: 1.2, expectancy: 50 },
    "60": { trade_count: 2, win_rate: 0.5, avg_r_multiple: 1.2, expectancy: 50 },
    "90": { trade_count: 2, win_rate: 0.5, avg_r_multiple: 1.2, expectancy: 50 },
  },
};

const TRADES = {
  trades: [
    {
      id: 1,
      ticker: "AAPL",
      entry_date: "2026-01-01T00:00:00",
      entry_price: 190,
      entry_thesis: "Breakout",
      signal_strength: 75,
      stop_loss: 185,
      target_1: 200,
      target_2: 210,
      entry_status: "TAKEN",
      skip_reason: null,
      actual_entry_date: "2026-01-01T00:00:00",
      actual_entry_price: 190,
      exit_date: "2026-01-05T00:00:00",
      exit_price: 200,
      exit_reason: "TP1",
      holding_days: 4,
      pnl: 100,
      pnl_pct: 0.0526,
      r_multiple: 2.0,
      notes: "",
      created_at: "2026-01-01T00:00:00",
    },
  ],
};

const MAE_MFE = {
  points: [
    {
      trade_id: 1,
      ticker: "AAPL",
      mae_pct: 0.02,
      mfe_pct: 0.06,
      pnl: 100,
      r_multiple: 2.0,
      exit_reason: "TP1",
    },
  ],
  warnings: [],
};

test.beforeEach(async ({ page }) => {
  await mockCoreRoutes(page);
});

test("renders decay charts, MAE/MFE distribution, and the trade log", async ({
  page,
}) => {
  await page.route(`${API_BASE}/api/v1/journal/summary`, (route) =>
    route.fulfill({ json: SUMMARY }),
  );
  await page.route(`${API_BASE}/api/v1/journal/decay**`, (route) =>
    route.fulfill({ json: DECAY }),
  );
  await page.route(`${API_BASE}/api/v1/journal/trades`, (route) =>
    route.fulfill({ json: TRADES }),
  );
  await page.route(`${API_BASE}/api/v1/journal/mae-mfe-distribution`, (route) =>
    route.fulfill({ json: MAE_MFE }),
  );

  await page.goto("/");
  await page.getByTestId("nav-journal").click();

  await expect(page.getByTestId("decay-win-rate-chart")).toBeVisible();
  await expect(page.getByTestId("decay-expectancy-chart")).toBeVisible();
  await expect(page.getByTestId("mae-mfe-scatter")).toBeVisible();
  await expect(page.getByTestId("journal-trades-table")).toBeVisible();
  await expect(page.getByTestId("journal-trade-row")).toContainText("AAPL");
});

test("shows empty states before any trade is closed", async ({ page }) => {
  await page.route(`${API_BASE}/api/v1/journal/summary`, (route) =>
    route.fulfill({ json: { ...SUMMARY, error: "No completed trades yet" } }),
  );
  await page.route(`${API_BASE}/api/v1/journal/decay**`, (route) =>
    route.fulfill({
      json: {
        windows: {
          "30": { trade_count: 0, win_rate: null, avg_r_multiple: null, expectancy: null },
        },
      },
    }),
  );
  await page.route(`${API_BASE}/api/v1/journal/trades`, (route) =>
    route.fulfill({ json: { trades: [] } }),
  );
  await page.route(`${API_BASE}/api/v1/journal/mae-mfe-distribution`, (route) =>
    route.fulfill({ json: { points: [], warnings: [] } }),
  );

  await page.goto("/");
  await page.getByTestId("nav-journal").click();

  await expect(page.getByTestId("decay-empty")).toBeVisible();
  await expect(page.getByTestId("mae-mfe-empty")).toBeVisible();
  await expect(page.getByTestId("journal-trades-empty")).toBeVisible();
});
