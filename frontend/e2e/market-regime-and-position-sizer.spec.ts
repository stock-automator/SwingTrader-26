import { test, expect } from "@playwright/test";
import { API_BASE, mockCoreRoutes } from "./helpers";

const LONG_SETUP_WITH_ATR = {
  ticker: "AAPL",
  direction: "LONG",
  tradable: true,
  as_of: "2026-01-01",
  close: 200.0,
  regime: "BULL_TREND",
  adx: 30.0,
  atr: 4.5,
  entry_price: 200.0,
  stop_loss: 190.0,
  take_profit: 220.0,
  shares: 5,
  risk_amount: 50.0,
  relative_strength: 0.1,
  rank: 1,
  note: "Breakout above 20d high",
  reward_risk_ratio: 2.0,
  notional_value: 1000.0,
};

test.beforeEach(async ({ page }) => {
  await mockCoreRoutes(page);
});

test("Market Traffic Light badge shows the current regime state with a tooltip", async ({
  page,
}) => {
  await page.route(`${API_BASE}/api/v1/market/regime`, (route) =>
    route.fulfill({
      json: {
        state: "BULL_CONFIRMED",
        spy_alignment: "BULLISH",
        qqq_alignment: "BULLISH",
        vix_level: 13.2,
        vix_regime: "LOW",
        breadth_pct: 72.0,
        breadth_above: 360,
        breadth_total: 500,
        notes: [],
      },
    }),
  );

  await page.goto("/");
  await page.getByTestId("nav-dashboard").click();

  const badge = page.getByTestId("market-regime-badge");
  await expect(badge).toBeVisible();
  await expect(badge).toContainText("BULL CONFIRMED");
  await expect(badge).toHaveAttribute("title", /VIX: 13.2 \(LOW\)/);
});

test("order ticket drawer shows a live ATR position sizer preview for the Live Screener", async ({
  page,
}) => {
  await page.route(`${API_BASE}/api/v1/screener/live**`, (route) =>
    route.fulfill({
      json: {
        setups: [LONG_SETUP_WITH_ATR],
        scanned: 1,
        skipped: 0,
        skip_reasons: {},
        warnings: [],
        macro_regime: "BULL_TRENDING",
        circuit_breaker_active: false,
      },
    }),
  );
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
  await page.route(`${API_BASE}/api/v1/position-sizer/preview`, async (route) => {
    const body = route.request().postDataJSON();
    // Mirror the real PositionSizer formula so the assertions below exercise
    // the actual risk-percentage wiring, not a canned number.
    const riskAmount = body.account_capital * body.risk_pct;
    const shares = Math.floor(riskAmount / (body.atr * body.atr_multiplier));
    await route.fulfill({ json: { shares, risk_amount: riskAmount } });
  });

  await page.goto("/");
  await page.getByTestId("nav-live-screener").click();
  await page.getByTestId("order-ticket-button").click();

  const sizer = page.getByTestId("atr-position-sizer");
  await expect(sizer).toBeVisible();
  // Default risk % is 1.0% on $1,000 = $10 risk / (4.5 ATR * 2.0x) = 1 share.
  await expect(page.getByTestId("atr-sizer-shares")).toHaveText("1");

  // Switching to 2.0% risk doubles the risk budget -> 2 shares.
  await page.getByTestId("risk-pct-0.02").click();
  await expect(page.getByTestId("atr-sizer-shares")).toHaveText("2");
});
