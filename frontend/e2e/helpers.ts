import type { Page } from "@playwright/test";

//: Shared backend mocks so every e2e spec starts from the same known-good
//: state instead of re-declaring health/sync/screener/signals routes -
//: individual specs only add or override the routes their scenario needs.
export const API_BASE = "http://localhost:8000";

export async function mockCoreRoutes(page: Page) {
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
      json: { generated_at: "2026-01-01T00:00:00Z", rows: [], scanned: 0, warnings: [] },
    }),
  );
}
