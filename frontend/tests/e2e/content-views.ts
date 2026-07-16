/**
 * Shared T2 content-view catalog + navigation helpers.
 *
 * Both the deterministic overflow gate (`content-layout.spec.ts`) and the softer
 * visual layer (`content-visual.spec.ts`) drive the SAME set of content views,
 * so the list of views and the "reach this content state" handshakes live here
 * once. Each view names a route, a role, a settled-state anchor, an optional
 * reach interaction, and the pseudo modes it should run.
 */

import type { Page } from "@playwright/test";
import type { PseudoMode } from "../pseudo/pseudo";
import type { Role } from "./content-fixtures";
import { CONTENT_IDS } from "./content-fixtures";

export const ALL_MODES: PseudoMode[] = ["baseline", "expand", "rtl"];

export interface ContentView {
  name: string;
  path: string;
  role: Role;
  /** Selector proving the content has settled (checked pre-transform). */
  ready: string;
  /** Reach the target content state (open a draft / select a lesson item). */
  reach?: (page: Page) => Promise<void>;
  /** Which pseudo modes to run. Native-script views run baseline-only. */
  modes: PseudoMode[];
}

/** Select a Player rail item by its (ASCII) section, then confirm it rendered. */
export function selectLesson(section: string) {
  return async (page: Page) => {
    await page.getByTestId("rail-item").filter({ hasText: section }).click();
    await page
      .locator('[data-testid="item-detail"]')
      .filter({ hasText: section })
      .waitFor({ state: "visible", timeout: 15_000 });
  };
}

/** Open the seeded draft in the Course Builder (same handshake as T1). */
export async function openDraft(page: Page) {
  await page.getByRole("combobox", { name: "Open an existing draft" }).click();
  await page.getByRole("option", { name: /Agentic AI Engineering/ }).click();
}

const PLAYER_PATH = `/learn/courses/${CONTENT_IDS.ENROLLMENT_ID}`;
const ITEM_DETAIL = '[data-testid="item-detail"]';

export const CONTENT_VIEWS: ContentView[] = [
  // ---- Course Builder item editor: objective texts + item titles ----
  {
    name: "builder-content",
    path: "/builder",
    role: "architect",
    ready: '[data-testid="objective-card"]',
    reach: openDraft,
    modes: ALL_MODES,
  },
  // ---- Course Player lesson view (LessonContent → AssetContent) ----
  {
    name: "player-markdown",
    path: PLAYER_PATH,
    role: "learner",
    ready: ITEM_DETAIL,
    reach: selectLesson("Long Markdown Reading"),
    modes: ALL_MODES,
  },
  {
    name: "player-objectives",
    path: PLAYER_PATH,
    role: "learner",
    ready: ITEM_DETAIL,
    reach: selectLesson("Objectives List"),
    modes: ALL_MODES,
  },
  {
    name: "player-rubric",
    path: PLAYER_PATH,
    role: "learner",
    ready: ITEM_DETAIL,
    reach: selectLesson("Rubric Rows"),
    modes: ALL_MODES,
  },
  {
    name: "player-assessment",
    path: PLAYER_PATH,
    role: "learner",
    ready: ITEM_DETAIL,
    reach: selectLesson("Assessment Body"),
    modes: ALL_MODES,
  },
  // Native-script content: audit raw (baseline) — the raw-CJK / raw-RTL case.
  {
    name: "player-cjk",
    path: PLAYER_PATH,
    role: "learner",
    ready: ITEM_DETAIL,
    reach: selectLesson("Japanese Lesson"),
    modes: ["baseline"],
  },
  {
    name: "player-rtl",
    path: PLAYER_PATH,
    role: "learner",
    ready: ITEM_DETAIL,
    reach: selectLesson("Arabic Lesson"),
    modes: ["baseline"],
  },
];
