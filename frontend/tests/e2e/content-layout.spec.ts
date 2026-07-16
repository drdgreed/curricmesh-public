/**
 * Content-layout regime (T2) — the deterministic HARD GATE for rendered COURSE
 * CONTENT.
 *
 * T1 (`layout.spec.ts`) audited the UI chrome. T2 audits the *content* the
 * author writes and the learner reads: lesson bodies, objectives, assessments,
 * and rubrics, driven by hostile payloads (long unbroken tokens, CJK, RTL, long
 * multi-paragraph markdown — see `content-fixtures.ts`).
 *
 * For each content view × mode we: render from the stress fixtures, apply the
 * T1 pseudolocalization transform where relevant, run the T1 overflow detector,
 * and FAIL on any offender not in the accepted baseline.
 *
 *   - `baseline` — content as-authored (pre-existing overflow + native-script
 *                  guard; the CJK/RTL views run baseline-only, so their native
 *                  script is audited untransformed = the raw-CJK/RTL case).
 *   - `expand`   — pseudo expansion (~140%) piled on top of the payload.
 *   - `rtl`      — bidi mirroring of the content.
 *
 * Reuses `applyPseudo` + `detectOverflow` + `KNOWN_OFFENDERS` verbatim — no new
 * engine. Real content-overflow findings are recorded in `known-offenders.ts`
 * (annotated, not hidden) so the gate tracks them for a follow-up layout fix.
 */

import { test, expect, type Page } from "@playwright/test";
import { applyPseudo, type PseudoMode } from "../pseudo/pseudo";
import { detectOverflow, type Offender } from "../pseudo/overflow";
import { mockContentApi, loginAs } from "./content-fixtures";
import { CONTENT_VIEWS, type ContentView } from "./content-views";
import {
  KNOWN_OFFENDERS,
  normalizeSelector,
  type KnownOffender,
} from "./known-offenders";

function report(view: string, mode: PseudoMode, offenders: Offender[]): string {
  const lines = offenders.map(
    (o) => `    • [${o.kind}] ${o.selector}\n        "${o.text}"`
  );
  return (
    `Content overflow on "${view}" (${mode}) — ${offenders.length} offender(s):\n` +
    lines.join("\n")
  );
}

async function loadView(page: Page, view: ContentView, mode: PseudoMode) {
  await mockContentApi(page);
  await loginAs(page, view.role);
  await page.goto(view.path);
  if (view.reach) await view.reach(page);
  await page.waitForSelector(view.ready, { state: "visible", timeout: 15_000 });
  await page.waitForLoadState("networkidle");
  await page.evaluate(applyPseudo, mode);
  await page.waitForTimeout(200);
}

function isKnown(o: Offender, accepted: KnownOffender[]): boolean {
  const sel = normalizeSelector(o.selector);
  return accepted.some(
    (k) => k.kind === o.kind && normalizeSelector(k.selector) === sel
  );
}

for (const view of CONTENT_VIEWS) {
  for (const mode of view.modes) {
    test(`content overflow gate — ${view.name} [${mode}]`, async ({
      page,
    }, testInfo) => {
      await loadView(page, view, mode);
      const offenders = await page.evaluate(detectOverflow);

      const accepted = KNOWN_OFFENDERS[`${view.name}:${mode}`] ?? [];
      const known = offenders.filter((o) => isKnown(o, accepted));
      const fresh = offenders.filter((o) => !isKnown(o, accepted));

      if (known.length > 0) {
        testInfo.annotations.push({
          type: "known-overflow",
          description: report(view.name, mode, known),
        });
      }

      expect(fresh, report(view.name, mode, fresh)).toEqual([]);
    });
  }
}
