/**
 * Playwright config — Sprint 4 Aşama C automation.
 *
 * The specs run against a deployed environment, NOT a local `pnpm
 * dev`. Set ``E2E_BASE_URL`` to the URL the browser should hit
 * (defaults to ``https://app.bluedev.dev`` for prod smoke; staging
 * runs override with the staging URL).
 *
 * Each spec also reads ``E2E_TEST_EMAIL`` / ``E2E_TEST_PASSWORD`` for
 * the auth fixture so we never check credentials in. CI provides them
 * via secrets, devs invoke with `--env-file .env.e2e.local`.
 *
 * Why not pin `webServer`: the API + Postgres + Redis + Iyzico
 * webhooks chain needs the full stack — easier to spin those up via
 * `docker compose` (or talk to staging) than to manage from
 * Playwright.
 */

import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.E2E_BASE_URL ?? 'https://app.bluedev.dev';

export default defineConfig({
  testDir: './e2e',
  // Timeouts kept generous — saga generation can run 30-60s end-to-end.
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
  ],
  use: {
    baseURL,
    actionTimeout: 10_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    // Locale matches the default the FE picks for new users.
    locale: 'tr-TR',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    // Mobile lane (Pixel 7) lives in a follow-up PR once the desktop
    // suite is stable in CI — adds redundancy + responsive coverage.
  ],
});
