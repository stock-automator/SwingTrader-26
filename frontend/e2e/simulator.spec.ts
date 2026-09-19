import { test, expect } from "@playwright/test";
import { API_BASE, mockCoreRoutes } from "./helpers";

const CANDIDATE = {
  ticker: "AAPL",
  direction: "LONG",
  tradable: true,
  as_of: "2025-06-02",
  close: 190.0,
  regime: "BULL_TREND",
  adx: 28.0,
  atr: 3.2,
  entry_price: 190.0,
  stop_loss: 182.0,
  take_profit: 210.0,
  shares: 6,
  risk_amount: 48.0,
  relative_strength: 1.2,
  rank: 1,
  note: "Donchian breakout",
  reward_risk_ratio: 2.5,
  notional_value: 1140.0,
};

const UNTRADABLE_CANDIDATE = {
  ...CANDIDATE,
  ticker: "MSFT",
  tradable: false,
  note: "Below minimum reward/risk",
};

function makeBars(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    date: `2025-05-${String(i + 1).padStart(2, "0")}`,
    open: 180 + i,
    high: 182 + i,
    low: 178 + i,
    close: 181 + i,
    volume: 1_000_000,
  }));
}

test.beforeEach(async ({ page }) => {
  await mockCoreRoutes(page);
  await page.route(`${API_BASE}/api/v1/backtest/historical-date-scan`, (route) =>
    route.fulfill({
      json: {
        target_date: "2025-06-02",
        setups: [CANDIDATE, UNTRADABLE_CANDIDATE],
        scanned: 2,
        skipped: 0,
        skip_reasons: {},
        warnings: [],
      },
    }),
  );
  await page.route(`${API_BASE}/api/v1/backtest/bars**`, (route) =>
    route.fulfill({ json: { ticker: "AAPL", bars: makeBars(20) } }),
  );
});

async function runScan(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByTestId("nav-simulator").click();
  await page.getByTestId("sim-target-date").fill("2025-06-02");
  await page.getByTestId("sim-scan-button").click();
  await expect(page.getByTestId("sim-candidates-table")).toBeVisible();
}

test("historical date scan lists candidates with tradable and untradable rows", async ({
  page,
}) => {
  await runScan(page);

  const rows = page.getByTestId("sim-candidate-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("AAPL");
  await expect(rows.nth(1)).toContainText("MSFT");

  // Untradable candidates render their Simulate Trade button disabled.
  await expect(rows.nth(1).getByTestId("sim-select-button")).toBeDisabled();
});

test("blind tape walkthrough hides future bars until stepped forward", async ({
  page,
}) => {
  await runScan(page);
  await page.getByTestId("sim-blind-tape-toggle").check();
  await page.getByTestId("sim-candidate-row").first().getByTestId("sim-select-button").click();

  await expect(page.getByTestId("simulate-trade-drawer")).toBeVisible();

  // Selecting a candidate opens the trade drawer AND kicks off the
  // zero-lookahead bars fetch for the chart below it; the drawer's
  // full-viewport backdrop is modal (by design, matching OrderTicketDrawer),
  // so close it to reach the Blind Tape panel underneath - `bars` state
  // lives in Simulator, not the drawer, so closing doesn't lose it.
  await page.getByRole("button", { name: "✕" }).click();
  await expect(page.getByTestId("simulate-trade-drawer")).toHaveClass(/translate-x-full/);

  // 20 bars are available but the walkthrough starts revealed to only the
  // first one - stepping forward reveals one more bar at a time without
  // re-fetching (client-side reveal window over the already-fetched series).
  const chart = page.locator("canvas").first();
  await expect(chart).toBeVisible();

  const stepButton = page.getByTestId("sim-step-forward");
  await expect(stepButton).toBeEnabled();
  await stepButton.click();
  await stepButton.click();
  await expect(stepButton).toBeEnabled();
});

test("simulating a trade resolves an exit and enables journal logging", async ({
  page,
}) => {
  await page.route(
    `${API_BASE}/api/v1/backtest/simulate-trade-execution`,
    (route) =>
      route.fulfill({
        json: {
          ticker: "AAPL",
          execution_mode: "NEXT_OPEN",
          fill_date: "2025-06-03",
          fill_price: 190.5,
          reference_price: 190.0,
          slippage_pct_applied: 0.0005,
          spread_pct: 0.0004,
          market_impact_pct: 0.0,
          spread_variance_pct: 0.0001,
          slippage_cost: 0.57,
          commission_cost: 1.14,
          total_cost: 1.71,
          shares: 6,
          stop_loss: 182.0,
          take_profit: 210.0,
          risk_amount: 48.0,
          reward_risk_ratio: 2.5,
          notional_value: 1143.0,
          tradable: true,
          note: null,
          realized_pnl_dollars: 116.4,
          realized_pnl_pct: 0.0611,
          holding_period_days: 5,
          exit_trigger: "TARGET",
          mae_pct: 0.012,
          mfe_pct: 0.071,
          exit_date: "2025-06-10",
          exit_price: 210.0,
        },
      }),
  );
  let journalBody: Record<string, unknown> | null = null;
  await page.route(`${API_BASE}/api/v1/journal/simulate`, (route) => {
    journalBody = route.request().postDataJSON();
    return route.fulfill({
      json: {
        id: 1,
        ticker: "AAPL",
        entry_date: "2025-06-03",
        entry_price: 190.5,
        exit_date: "2025-06-10",
        exit_price: 210.0,
        exit_reason: "TARGET",
        holding_days: 5,
      },
    });
  });

  await runScan(page);
  await page.getByTestId("sim-candidate-row").first().getByTestId("sim-select-button").click();
  await page.getByTestId("sim-run-button").click();

  await expect(page.getByTestId("sim-realized-outcome")).toBeVisible();
  await expect(page.getByTestId("sim-exit-trigger-badge")).toHaveText("TARGET");

  const logButton = page.getByTestId("sim-log-journal-button");
  await expect(logButton).toBeDisabled();

  await page.getByTestId("sim-postmortem-note").fill(
    "Took the breakout, held for the full target - discipline paid off.",
  );
  await expect(logButton).toBeEnabled();
  await logButton.click();

  await expect(page.getByText(/Logged AAPL to journal \(MANUAL_SIMULATION\)/)).toBeVisible();
  expect(journalBody).toMatchObject({
    ticker: "AAPL",
    exit_trigger: "TARGET",
    post_mortem_note: expect.stringContaining("discipline"),
  });
});
