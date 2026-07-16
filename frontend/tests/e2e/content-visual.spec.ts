/**
 * Content visual regression (T2) — the SOFTER signal for rendered content.
 *
 * A full-page screenshot per content view × mode. The overflow detector in
 * `content-layout.spec.ts` is the deterministic gate; these screenshots catch
 * subtler content-rendering regressions (wrapping, bidi mirroring, bullet/rubric
 * alignment) a boolean audit can miss.
 *
 * Like T1's `visual.spec.ts`, baselines are platform-specific, so these skip on
 * CI and are meant to be run + updated locally (`npm run test:e2e:update`).
 */

import { test, expect } from "@playwright/test";
import { applyPseudo } from "../pseudo/pseudo";
import { mockContentApi, loginAs } from "./content-fixtures";
import { CONTENT_VIEWS } from "./content-views";

test.skip(!!process.env.CI, "visual baselines are platform-specific; run locally");

for (const view of CONTENT_VIEWS) {
  for (const mode of view.modes) {
    test(`content visual — ${view.name} [${mode}]`, async ({ page }) => {
      await mockContentApi(page);
      await loginAs(page, view.role);
      await page.goto(view.path);
      if (view.reach) await view.reach(page);
      await page.waitForSelector(view.ready, {
        state: "visible",
        timeout: 15_000,
      });
      await page.waitForLoadState("networkidle");
      await page.evaluate(applyPseudo, mode);
      await page.waitForTimeout(200);
      await expect(page).toHaveScreenshot(`${view.name}-${mode}.png`, {
        fullPage: true,
        animations: "disabled",
      });
    });
  }
}
