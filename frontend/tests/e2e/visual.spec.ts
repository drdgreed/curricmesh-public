/**
 * Visual regression (T1) — the SOFTER signal.
 *
 * A full-page screenshot per screen × mode. The overflow detector in
 * layout.spec.ts is the deterministic gate; these screenshots catch subtler
 * regressions (wrapping, alignment) a boolean audit can miss.
 *
 * Baselines are platform-specific (Playwright suffixes them with the OS), so
 * font rendering differs between a dev's macOS and Linux CI. To keep the
 * deterministic gate as the required signal and avoid cross-platform flakiness,
 * visual assertions are skipped on CI — run + update them locally.
 */

import { test, expect, type Page } from "@playwright/test";
import { applyPseudo, type PseudoMode } from "../pseudo/pseudo";
import { mockApi, loginAs, type Role } from "./fixtures";

test.skip(!!process.env.CI, "visual baselines are platform-specific; run locally");

const MODES: PseudoMode[] = ["baseline", "expand", "rtl"];

interface Screen {
  name: string;
  path: string;
  role: Role | null;
  ready: string;
  reach?: (page: Page) => Promise<void>;
}

const SCREENS: Screen[] = [
  { name: "login", path: "/login", role: null, ready: "text=Sign in" },
  { name: "dashboard", path: "/", role: "architect", ready: "text=Curricula" },
  {
    name: "course-builder",
    path: "/builder",
    role: "architect",
    ready: "text=Course Builder",
  },
  {
    name: "course-player",
    path: "/course",
    role: "learner",
    ready: "text=Browse the active version",
  },
];

for (const screen of SCREENS) {
  for (const mode of MODES) {
    test(`visual — ${screen.name} [${mode}]`, async ({ page }) => {
      await mockApi(page);
      if (screen.role) await loginAs(page, screen.role);
      await page.goto(screen.path);
      if (screen.reach) await screen.reach(page);
      await page.waitForSelector(screen.ready, { state: "visible", timeout: 15_000 });
      await page.waitForLoadState("networkidle");
      await page.evaluate(applyPseudo, mode);
      await page.waitForTimeout(200);
      await expect(page).toHaveScreenshot(`${screen.name}-${mode}.png`, {
        fullPage: true,
        animations: "disabled",
        // The AI-spend tile shows fixture numbers; mask it so any future
        // wiring to live cost data does not churn the baseline.
        mask: [page.locator('[data-testid="ai-spend-tile"]')],
      });
    });
  }
}
