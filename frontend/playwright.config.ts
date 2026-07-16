import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the translation-effects layout regime (T1).
 *
 * Runs real Chromium against a production `vite preview` of the built app
 * (jsdom/vitest cannot measure layout). The webServer builds + serves the app
 * so `npx playwright test` is one command; locally an already-running preview
 * is reused.
 */
const PORT = 4173;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI
    ? [["github"], ["list"], ["html", { open: "never" }]]
    : [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    viewport: { width: 1280, height: 800 },
    trace: "on-first-retry",
  },
  expect: {
    // Allow a small pixel budget for anti-aliasing on the softer visual signal.
    toHaveScreenshot: { maxDiffPixelRatio: 0.02 },
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: `npm run build && npm run preview -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
  },
});
