/**
 * API mocking + auth helpers (T1).
 *
 * The layout regime is self-contained: every screen renders from
 * route-intercepted fixtures (Playwright `page.route`) — NO real backend is
 * stood up. The fixtures mirror the shapes the frontend expects (see
 * `frontend/src/api/*.ts`). `loginAs` seeds the auth token/role the way the app
 * reads it (localStorage, consumed by AuthContext on mount) so RequireAuth
 * passes and the chrome renders the role/org.
 */

import type { Page } from "@playwright/test";

export type Role = "architect" | "program_manager" | "instructor" | "learner";

const ORG_ID = "org-acme-001";
const ORG_NAME = "Acme Learning Collective";
const CURRICULUM_ID = "curr-agentic-ai-eng";
const COURSE_ID = "course-draft-001";

// ---------------------------------------------------------------------------
// Fixture payloads (representative, deterministic — no timestamps that drift)
// ---------------------------------------------------------------------------

const NOW = "2026-07-07T12:00:00Z";

const courseOut = {
  id: COURSE_ID,
  title: "Agentic AI Engineering",
  description:
    "Design, build, and operate production-grade agentic systems end to end.",
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
    id: "obj-1",
    draft_course_id: COURSE_ID,
    text: "Explain the agent loop and tool-use contract",
    bloom_level: "understand",
    key_skills: ["agent-loop", "tool-use"],
    week_index: 1,
    order_index: 0,
  },
  {
    id: "obj-2",
    draft_course_id: COURSE_ID,
    text: "Design a retrieval-augmented tutor with guardrails",
    bloom_level: "create",
    key_skills: ["rag", "guardrails", "evaluation"],
    week_index: 2,
    order_index: 1,
  },
];

const items = [
  {
    id: "item-1",
    draft_course_id: COURSE_ID,
    kind: "reading",
    title: "The Agent Loop: Perceive, Plan, Act",
    content: "A grounding reading on the core control loop of an agent.",
    source_url: "https://example.com/agent-loop",
    metrics: { words: 1800 },
    week_index: 1,
    order_index: 0,
    estimated_minutes: 18,
  },
  {
    id: "item-2",
    draft_course_id: COURSE_ID,
    kind: "exercise",
    title: "Build a tool-calling agent",
    content: "Hands-on: wire a model to two tools and observe the loop.",
    source_url: null,
    metrics: { loc: 120 },
    week_index: 2,
    order_index: 1,
    estimated_minutes: 45,
  },
];

const mediaAssets = [
  {
    id: "media-1",
    kind: "video",
    filename: "agent-loop-walkthrough-final-v3.mp4",
    mime: "video/mp4",
    size_bytes: 84_000_000,
    checksum: "abc123",
    duration_s: 742,
    status: "ready",
    storage_key: "org/media-1",
    created_at: NOW,
  },
];

const calendarTile = (
  id: string,
  kind: string,
  label: string,
  misaligned = false
) => ({
  id,
  lineage_key: `lin-${id}`,
  kind,
  label,
  source_url: kind === "reading" ? "https://example.com/x" : null,
  latest_version: "1.2.0",
  status: "active",
  misaligned,
});

const dashboard = {
  curricula: [
    {
      id: CURRICULUM_ID,
      name: "Agentic AI Engineering",
      slug: "agentic-ai-engineering",
      current_version_id: "ver-2",
      versions: [
        { id: "ver-1", semver: "1.0.0", status: "released", created_at: NOW },
        { id: "ver-2", semver: "1.1.0", status: "active", created_at: NOW },
      ],
      cohorts: [
        {
          id: "cohort-1",
          name: "Summer 2026",
          version_id: "ver-2",
          start_date: "2026-06-01",
          end_date: "2026-09-01",
        },
      ],
      alignment: [],
    },
  ],
  recent_events: [
    {
      id: "ev-1",
      event_type: "version.released",
      target: CURRICULUM_ID,
      actor_id: "user-1",
      actor_label: "Dana Architect",
      target_label: "Agentic AI Engineering v1.1.0",
      details: {},
      created_at: NOW,
    },
  ],
};

const alignment = {
  items: [
    {
      dependent_id: "asset-guardrails",
      dependent_label: "Guardrails & Evaluation",
      prerequisite_id: "asset-agent-loop",
      prerequisite_label: "The Agent Loop",
      mode: "revision" as const,
      revision_delta: 2,
    },
  ],
};

const calendar = {
  curriculum_id: CURRICULUM_ID,
  sections: [
    {
      week_index: 1,
      section: "Foundations of the Agent Loop",
      tiles: [
        calendarTile("asset-agent-loop", "reading", "The Agent Loop"),
        calendarTile("asset-tools", "exercise", "Tool-calling exercise"),
      ],
    },
    {
      week_index: 2,
      section: "Retrieval-Augmented Tutors",
      tiles: [
        calendarTile("asset-rag", "reading", "RAG patterns", true),
        calendarTile("asset-eval", "assessment", "Evaluation checkpoint"),
      ],
    },
    {
      week_index: 0,
      section: "Projects",
      tiles: [calendarTile("asset-capstone", "project", "Capstone: ship an agent")],
    },
  ],
};

const aiUsage = {
  total_calls: 128,
  total_cost_usd: 4.37,
  persisted: {
    total_calls: 128,
    total_input_tokens: 512_000,
    total_output_tokens: 98_000,
    total_cost_usd: 4.37,
    by_model: {
      "claude-sonnet": {
        calls: 128,
        input_tokens: 512_000,
        output_tokens: 98_000,
        cost_usd: 4.37,
      },
    },
    by_day: [{ date: "2026-07-06", calls: 128, cost_usd: 4.37 }],
  },
};

const effort = {
  by_week: {
    "1": { student_minutes: 180, item_count: 2 },
    "2": { student_minutes: 240, item_count: 2 },
  },
  total_student_minutes: 420,
};

const overload = [
  {
    week: 1,
    student_hours: 3,
    overload: false,
    new_concepts: 3,
    density_warn: false,
  },
  {
    week: 2,
    student_hours: 4,
    overload: true,
    new_concepts: 5,
    density_warn: true,
  },
];

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

type Json = unknown;

/** Resolve an API path (already stripped of the /api/v1 prefix) to a fixture. */
function resolve(path: string, method: string): Json {
  // exact, order-sensitive matches first
  if (path === "/auth/login") return { access_token: "test.jwt.token", token_type: "bearer" };
  if (path === "/auth/me")
    return { sub: "user-1", role: "architect", org: ORG_ID, org_name: ORG_NAME };
  if (path === "/dashboard") return dashboard;
  if (path === "/internal/ai-usage") return aiUsage;
  if (path === "/analytics/overview")
    return { velocity: [], time_in_state: [], cadence: { releases: 0, mean_days_between: null, median_days_between: null }, distribution: [] };
  if (path === "/ai/inbox") return { items: [] };
  if (path === "/media") return mediaAssets;
  if (path === "/builder/courses") return method === "POST" ? courseOut : [courseOut];

  // parameterised
  if (/\/curricula\/[^/]+\/alignment$/.test(path)) return alignment;
  if (/\/curricula\/[^/]+\/calendar$/.test(path)) return calendar;
  if (/\/builder\/courses\/[^/]+\/objectives$/.test(path)) return objectives;
  if (/\/builder\/courses\/[^/]+\/items$/.test(path)) return items;
  if (/\/builder\/courses\/[^/]+\/effort$/.test(path)) return effort;
  if (/\/builder\/courses\/[^/]+\/overload$/.test(path)) return overload;
  if (/\/builder\/courses\/[^/]+\/advisor-notes$/.test(path)) return [];
  if (/\/builder\/courses\/[^/]+$/.test(path)) return courseOut;
  if (/\/builder\/items\/[^/]+\/media$/.test(path)) return [];
  if (/\/ccrs\/[^/]+\/gate$/.test(path)) return { has_change_set: false, qa_passed: false, approval_count: 0, has_instructor_approval: false, can_release: false };

  // safe default: empty list / object so no screen hard-errors
  return [];
}

/**
 * Intercept every `/api/v1/**` request and fulfill it from static fixtures.
 * Must be called before `page.goto`.
 */
export async function mockApi(page: Page): Promise<void> {
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

/**
 * Seed the auth session the way AuthContext reads it, so RequireAuth passes and
 * the chrome shows the role/org without hitting the network. Must be called
 * before `page.goto`.
 */
export async function loginAs(page: Page, role: Role): Promise<void> {
  await page.addInitScript(
    ([r, orgId, orgName]) => {
      localStorage.setItem("auth_token", "test.jwt.token");
      localStorage.setItem("auth_role", r);
      localStorage.setItem("auth_org", orgId);
      localStorage.setItem("auth_org_name", orgName);
    },
    [role, ORG_ID, ORG_NAME] as const
  );
}

export const IDS = { ORG_ID, ORG_NAME, CURRICULUM_ID, COURSE_ID };
