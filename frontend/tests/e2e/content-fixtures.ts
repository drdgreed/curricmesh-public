/**
 * Content stress fixtures (T2).
 *
 * T1 (`fixtures.ts`) proved the UI *chrome* survives translation. T2 proves the
 * rendered *course content* — lesson bodies, objectives, assessments, rubrics —
 * survives long / CJK / RTL payloads. Same regime: route-intercept every
 * `/api/v1/**` call and fulfill from static fixtures; NO backend, NO DB.
 *
 * We reuse T1's `loginAs` / `IDS` verbatim (auth seeding is identical) and only
 * add the *content-bearing* endpoints with deliberately hostile payloads:
 *
 *   - a very long UNBROKEN token (URL-like + a made-up long word) — the classic
 *     "won't wrap, overflows its box" failure,
 *   - a CJK sample (Japanese, no spaces) — must wrap per-character, never clip,
 *   - an RTL sample (Arabic) — must mirror without breaking layout,
 *   - long multi-paragraph markdown — vertical flow / clipped-y guard.
 *
 * These feed two surfaces:
 *   1. the Course Builder item editor (objective texts + item titles), and
 *   2. the Course Player lesson view (`LessonContent` → `AssetContent`), which
 *      renders every content shape: markdown, learning_objectives, rubric.
 */

import type { Page } from "@playwright/test";
import { loginAs, IDS, type Role } from "./fixtures";

export { loginAs, type Role };

const NOW = "2026-07-07T12:00:00Z";
const COURSE_ID = IDS.COURSE_ID;
const ENROLLMENT_ID = "enr-stress-001";

// ---------------------------------------------------------------------------
// Stress payloads (deterministic — no drift)
// ---------------------------------------------------------------------------

/** A single unbroken token far wider than any content column (~130 chars). */
export const LONG_WORD =
  "Supercalifragilisticexpialidocious" +
  "Antidisestablishmentarianism" +
  "Pneumonoultramicroscopicsilicovolcanoconiosis" +
  "Floccinaucinihilipilification";

/** A long URL with no spaces — `/` is not a default line-break opportunity. */
export const LONG_URL =
  "https://curricmesh.example.com/agentic-ai/production/deployment/" +
  "observability/guardrails/evaluation/reference/appendix/section-42/" +
  "unbreakable-canonical-slug-that-keeps-going-and-going";

/** Japanese, no inter-word spaces — must break between CJK glyphs, not clip. */
export const CJK_TEXT =
  "人工知能エージェントは環境を知覚し計画を立案し行動を実行し結果を観察して" +
  "次の行動を決定する制御ループを繰り返し実行することで複雑なタスクを自律的に" +
  "遂行します。ツール使用の契約と検索拡張生成による根拠付けが信頼性の鍵です。" +
  "本レッスンでは本番環境で稼働するエージェントの設計と評価の実務を扱います。";

/** Arabic (RTL, with natural word spaces) — tests bidi mirroring. */
export const RTL_TEXT =
  "الذكاء الاصطناعي الوكيل يدرك البيئة ثم يضع خطة وينفذ الإجراءات ويلاحظ " +
  "النتائج ليقرر الخطوة التالية ضمن حلقة تحكم متكررة. إن عقد استخدام الأدوات " +
  "والتوليد المعزز بالاسترجاع هما مفتاح الموثوقية في الأنظمة الوكيلة الإنتاجية.";

/** Long multi-paragraph markdown (headings + bullets + paragraphs). */
export const LONG_MARKDOWN = [
  "# The Agent Control Loop",
  "",
  "An agent perceives its environment, plans a next step, acts through a tool,",
  "and observes the result — then repeats. Production systems layer retries,",
  "guardrails, evaluation, and observability on top of that bare loop so the",
  "behaviour stays bounded and debuggable under real traffic.",
  "",
  "## Why the contract matters",
  "",
  "The tool-use contract is the seam between an unpredictable model and your",
  "deterministic systems. Get it wrong and every downstream guarantee leaks.",
  "",
  "- Perceive: normalise the world into a compact, typed observation",
  "- Plan: choose the next tool call from a constrained, documented schema",
  "- Act: execute with idempotency keys and a hard timeout budget",
  "- Observe: fold the result back into state; emit a structured trace",
  "",
  "## Reference",
  "",
  `See the full appendix at ${LONG_URL} — and note the canonical identifier`,
  `token ${LONG_WORD} that must never force a horizontal scrollbar.`,
].join("\n");

const ASSESSMENT_BODY = [
  "## Prompt",
  "",
  "Design and defend a production agent for automated incident triage.",
  "Explain how your tool-use contract, guardrails, and evaluation harness keep",
  "the loop bounded. Reference the canonical spec identifier",
  `${LONG_WORD} and the appendix ${LONG_URL} in your write-up.`,
  "",
  "## Rubric",
  "",
  "- Correctness of the control-loop model (perceive/plan/act/observe)",
  "- Soundness of the tool-use contract and idempotency handling",
  "- Quality of the evaluation + guardrail strategy",
].join("\n");

// ---------------------------------------------------------------------------
// Builder fixtures (objective texts + item titles carry the stress payloads)
// ---------------------------------------------------------------------------

const courseOut = {
  id: COURSE_ID,
  // Title kept as T1's so the "open an existing draft" reach matches /Agentic AI Engineering/.
  title: "Agentic AI Engineering",
  description: "Design, build, and operate production-grade agentic systems.",
  learner_profile: {
    experience_level: "intermediate",
    role: "Backend Engineer",
    goals: "Ship a production agent",
  },
  effort_config: { present_min_per_slide: 2 },
  target_weeks: 6,
  status: "draft",
  curriculum_id: null,
  created_at: NOW,
};

const objectives = [
  {
    id: "obj-long",
    draft_course_id: COURSE_ID,
    text: `Explain the canonical identifier ${LONG_WORD} without wrapping failure`,
    bloom_level: "understand",
    key_skills: ["agent-loop", "tool-use-contract"],
    week_index: 1,
    order_index: 0,
  },
  {
    id: "obj-url",
    draft_course_id: COURSE_ID,
    text: `Read the deployment reference at ${LONG_URL}`,
    bloom_level: "apply",
    key_skills: ["deployment"],
    week_index: 1,
    order_index: 1,
  },
  {
    id: "obj-cjk",
    draft_course_id: COURSE_ID,
    text: CJK_TEXT,
    bloom_level: "analyze",
    key_skills: ["rag"],
    week_index: 2,
    order_index: 2,
  },
  {
    id: "obj-rtl",
    draft_course_id: COURSE_ID,
    text: RTL_TEXT,
    bloom_level: "create",
    key_skills: ["guardrails"],
    week_index: 2,
    order_index: 3,
  },
];

const items = [
  {
    id: "item-long",
    draft_course_id: COURSE_ID,
    kind: "references",
    title: `Reference: ${LONG_WORD}`,
    content: LONG_MARKDOWN,
    source_url: LONG_URL,
    metrics: { words: 1800 },
    week_index: 1,
    order_index: 0,
    estimated_minutes: 18,
  },
  {
    id: "item-cjk",
    draft_course_id: COURSE_ID,
    kind: "lab",
    title: CJK_TEXT.slice(0, 60),
    content: CJK_TEXT,
    source_url: null,
    metrics: { loc: 120 },
    week_index: 2,
    order_index: 1,
    estimated_minutes: 45,
  },
];

// ---------------------------------------------------------------------------
// Player fixtures — one item per content shape, each with a distinct ASCII
// `section` used to target its rail row / detail header in the spec.
// ---------------------------------------------------------------------------

interface FixtureItem {
  member_id: string;
  section: string;
  week_index: number;
  order: number;
  kind: string;
  lineage_key: string;
  content: string;
  media: never[];
  progress_status: string;
}

const mk = (
  member_id: string,
  section: string,
  kind: string,
  content: string,
  order: number
): FixtureItem => ({
  member_id,
  section,
  week_index: 1,
  order,
  kind,
  lineage_key: `lin-${member_id}`,
  content,
  media: [],
  progress_status: "not_started",
});

const playerItems: FixtureItem[] = [
  mk("m-md", "Long Markdown Reading", "references", LONG_MARKDOWN, 0),
  mk(
    "m-obj",
    "Objectives List",
    "learning_objectives",
    JSON.stringify([
      { text: `Trace the control loop through ${LONG_WORD}` },
      { text: CJK_TEXT },
      { text: RTL_TEXT },
      {
        text: "Operate the agent in production",
        children: [
          { text: `Wire observability for ${LONG_URL}` },
          { text: "Bound the loop with a timeout budget" },
        ],
      },
    ]),
    1
  ),
  mk(
    "m-rubric",
    "Rubric Rows",
    "rubric",
    JSON.stringify({
      criteria: [
        {
          name: `Control-loop correctness incl. ${LONG_WORD}`,
          weight: 0.4,
        },
        { name: CJK_TEXT, weight: 0.3 },
        { name: RTL_TEXT, weight: 0.3 },
      ],
    }),
    2
  ),
  mk("m-assess", "Assessment Body", "assessment", ASSESSMENT_BODY, 3),
  mk("m-cjk", "Japanese Lesson", "lab", CJK_TEXT + "\n\n" + CJK_TEXT, 4),
  mk("m-rtl", "Arabic Lesson", "project", RTL_TEXT + "\n\n" + RTL_TEXT, 5),
];

const courseStructure = {
  enrollment_id: ENROLLMENT_ID,
  curriculum_version_id: "ver-2",
  title: "Agentic AI Engineering",
  status: "active",
  completed_items: 0,
  total_items: playerItems.length,
  items: playerItems,
};

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

type Json = unknown;

function resolve(path: string, method: string): Json {
  if (path === "/auth/login")
    return { access_token: "test.jwt.token", token_type: "bearer" };
  if (path === "/auth/me")
    return {
      sub: "user-1",
      role: "architect",
      org: IDS.ORG_ID,
      org_name: IDS.ORG_NAME,
    };
  if (path === "/media") return [];
  if (path === "/builder/courses")
    return method === "POST" ? courseOut : [courseOut];

  // Player: pinned course structure for the stress enrollment.
  if (/\/learn\/courses\/[^/]+$/.test(path)) return courseStructure;

  // Builder canvas endpoints.
  if (/\/builder\/courses\/[^/]+\/objectives$/.test(path)) return objectives;
  if (/\/builder\/courses\/[^/]+\/items$/.test(path)) return items;
  if (/\/builder\/courses\/[^/]+\/effort$/.test(path))
    return { by_week: {}, total_student_minutes: 0 };
  if (/\/builder\/courses\/[^/]+\/overload$/.test(path)) return [];
  if (/\/builder\/courses\/[^/]+\/advisor-notes$/.test(path)) return [];
  if (/\/builder\/courses\/[^/]+$/.test(path)) return courseOut;
  if (/\/builder\/items\/[^/]+\/media$/.test(path)) return [];

  // Safe default: empty list/object so no screen hard-errors.
  return [];
}

/** Intercept `/api/v1/**` and fulfill from the stress fixtures. */
export async function mockContentApi(page: Page): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const body = resolve(path, route.request().method());
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
}

export const CONTENT_IDS = { COURSE_ID, ENROLLMENT_ID };
