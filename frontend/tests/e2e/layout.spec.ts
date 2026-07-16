/**
 * Translation-effects layout regime (T1) — the deterministic HARD GATE.
 *
 * For each key screen × each mode (baseline / expand / rtl): render from mocked
 * fixtures, apply the pseudolocalization DOM transform, run the overflow
 * detector, and FAIL listing the offenders if any text no longer fits its box.
 *
 * `expand` is the mode that surfaces translation expansion overflow; `baseline`
 * is a pre-existing-overflow guard; `rtl` catches mirroring breakage.
 */

import { test, expect, type Page } from "@playwright/test";
import { applyPseudo, type PseudoMode } from "../pseudo/pseudo";
import { detectOverflow, type Offender } from "../pseudo/overflow";
import { mockApi, loginAs, type Role } from "./fixtures";
import {
  KNOWN_OFFENDERS,
  normalizeSelector,
  type KnownOffender,
} from "./known-offenders";

const MODES: PseudoMode[] = ["baseline", "expand", "rtl"];

interface Screen {
  name: string;
  path: string;
  role: Role | null; // null => unauthenticated (login)
  /** A selector that must be visible before we audit (screen is settled). */
  ready: string;
  /** Optional extra interaction to reach the target state (e.g. open a draft). */
  reach?: (page: Page) => Promise<void>;
}

const SCREENS: Screen[] = [
  {
    name: "login",
    path: "/login",
    role: null,
    ready: "text=Sign in",
  },
  {
    name: "dashboard",
    path: "/",
    role: "architect",
    ready: "text=Curricula",
  },
  {
    name: "course-builder",
    path: "/builder",
    role: "architect",
    ready: "text=Course Builder",
  },
  {
    name: "course-builder-active",
    path: "/builder",
    role: "architect",
    ready: "text=Explain the agent loop",
    reach: async (page) => {
      await page
        .getByRole("combobox", { name: "Open an existing draft" })
        .click();
      await page
        .getByRole("option", { name: /Agentic AI Engineering/ })
        .click();
    },
  },
  {
    name: "course-player",
    path: "/course",
    role: "learner",
    ready: "text=Browse the active version",
  },
];

/** Format offenders for a readable failure message. */
function report(screen: string, mode: PseudoMode, offenders: Offender[]): string {
  const lines = offenders.map(
    (o) => `    • [${o.kind}] ${o.selector}\n        "${o.text}"`
  );
  return (
    `Layout overflow on "${screen}" (${mode}) — ${offenders.length} offender(s):\n` +
    lines.join("\n")
  );
}

async function loadScreen(page: Page, screen: Screen, mode: PseudoMode) {
  await mockApi(page);
  if (screen.role) await loginAs(page, screen.role);
  await page.goto(screen.path);
  // `reach` interactions (e.g. opening a draft) auto-wait for their targets;
  // run them first, then wait for the settled-state anchor.
  if (screen.reach) await screen.reach(page);
  await page.waitForSelector(screen.ready, { state: "visible", timeout: 15_000 });
  await page.waitForLoadState("networkidle");
  // Apply pseudolocalization to the rendered DOM, then let layout reflow.
  await page.evaluate(applyPseudo, mode);
  await page.waitForTimeout(200);
}

/** True if `o` matches a known-accepted offender (ignoring volatile ids). */
function isKnown(o: Offender, accepted: KnownOffender[]): boolean {
  const sel = normalizeSelector(o.selector);
  return accepted.some(
    (k) => k.kind === o.kind && normalizeSelector(k.selector) === sel
  );
}

for (const screen of SCREENS) {
  for (const mode of MODES) {
    test(`overflow gate — ${screen.name} [${mode}]`, async ({ page }, testInfo) => {
      await loadScreen(page, screen, mode);
      const offenders = await page.evaluate(detectOverflow);

      const accepted = KNOWN_OFFENDERS[`${screen.name}:${mode}`] ?? [];
      const known = offenders.filter((o) => isKnown(o, accepted));
      const fresh = offenders.filter((o) => !isKnown(o, accepted));

      // Record already-accepted findings (tech debt) without failing the gate.
      if (known.length > 0) {
        testInfo.annotations.push({
          type: "known-overflow",
          description: report(screen.name, mode, known),
        });
      }

      // The gate fails ONLY on new/regressed overflow beyond the baseline.
      expect(fresh, report(screen.name, mode, fresh)).toEqual([]);
    });
  }
}
