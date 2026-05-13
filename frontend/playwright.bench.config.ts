/**
 * Playwright config for benchmarks (lives outside frontend/tests/).
 *
 * Same browser + webServer wiring as `playwright.config.ts`, but points at
 * `../benchmarks/frontend/` and uses zero retries so a flaky frame budget
 * fails loud.
 */

import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.PORT ?? 5173);

export default defineConfig({
  testDir: "../benchmarks/frontend",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",

  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "on-first-retry",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: {
    command: "pnpm dev",
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
