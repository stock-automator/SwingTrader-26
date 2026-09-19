import { test, expect } from "@playwright/test";
import { API_BASE, mockCoreRoutes } from "./helpers";

const UNCONFIGURED = {
  telegram_bot_token: null,
  telegram_chat_id: null,
  discord_webhook_url: null,
  generic_webhook_url: null,
  telegram_configured: false,
  discord_configured: false,
  webhook_configured: false,
};

test.beforeEach(async ({ page }) => {
  await mockCoreRoutes(page);
});

test("saving a channel enables its test button, and a test alert toasts success", async ({
  page,
}) => {
  let savedBody: unknown = null;
  await page.route(`${API_BASE}/api/v1/alerts/config`, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ json: UNCONFIGURED });
    }
    savedBody = route.request().postDataJSON();
    return route.fulfill({
      json: {
        ...UNCONFIGURED,
        discord_webhook_url: "https://discord.example/hook",
        discord_configured: true,
      },
    });
  });
  await page.route(`${API_BASE}/api/v1/alerts/test`, (route) =>
    route.fulfill({ json: { channel: "discord", status: "sent" } }),
  );

  await page.goto("/");
  await page.getByTestId("nav-alerts").click();

  await expect(page.getByTestId("discord-test-button")).toBeDisabled();

  await page
    .getByTestId("discord-webhook-input")
    .fill("https://discord.example/hook");
  await page.getByTestId("alert-settings-save").click();

  await expect(page.getByText("Alert channel settings saved")).toBeVisible();
  await expect(page.getByTestId("discord-test-button")).toBeEnabled();
  expect((savedBody as { discord_webhook_url: string }).discord_webhook_url).toBe(
    "https://discord.example/hook",
  );

  await page.getByTestId("discord-test-button").click();
  await expect(page.getByText("Test alert sent to discord")).toBeVisible();
});

test("a failed test alert surfaces an error toast", async ({ page }) => {
  await page.route(`${API_BASE}/api/v1/alerts/config`, (route) =>
    route.fulfill({
      json: {
        ...UNCONFIGURED,
        generic_webhook_url: "https://example.com/hook",
        webhook_configured: true,
      },
    }),
  );
  await page.route(`${API_BASE}/api/v1/alerts/test`, (route) =>
    route.fulfill({
      status: 502,
      json: { detail: "Test alert failed: connection refused" },
    }),
  );

  await page.goto("/");
  await page.getByTestId("nav-alerts").click();
  await expect(page.getByTestId("webhook-test-button")).toBeEnabled();

  await page.getByTestId("webhook-test-button").click();
  await expect(page.getByText(/Test alert failed/)).toBeVisible();
});
