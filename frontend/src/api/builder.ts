/**
 * Course Builder API client (Task 8). Mirrors the backend contract under
 * ``/api/v1/builder`` (app/builder/router_course.py + router_publish.py). Reuses
 * the shared axios `apiClient` whose baseURL is `.../api/v1`, so every path here
 * is prefixed with `/builder`.
 *
 * verbatimModuleSyntax is on → the type surface is exported with `export type`
 * and consumers must `import type { … }` to avoid a runtime crash.
 */

import { apiClient } from "./client";

// ---------- types (mirror app/builder/schemas.py) ----------

export type BloomLevel =
  | "remember"
  | "understand"
  | "apply"
  | "analyze"
  | "evaluate"
  | "create";

export const BLOOM_LEVELS: BloomLevel[] = [
  "remember",
  "understand",
  "apply",
  "analyze",
  "evaluate",
  "create",
];

export type EdgeType = "prerequisite" | "supports";

export interface LearnerProfile {
  experience_level?: string | null;
  role?: string | null;
  goals?: string | null;
  weekly_hours_target?: number | null;
  in_class_hours_per_week?: number | null;
  motivation?: string | null;
}

export interface EffortConfig {
  present_min_per_slide?: number;
  review_min_per_slide?: number;
  study_words_per_minute?: number;
  min_per_100_loc?: number;
  min_per_problem?: number;
}

export interface CourseCreate {
  title: string;
  description?: string;
  learner_profile?: LearnerProfile;
  effort_config?: EffortConfig;
  target_weeks?: number;
}

export interface CourseUpdate {
  title?: string;
  description?: string;
  learner_profile?: LearnerProfile;
  effort_config?: EffortConfig;
  target_weeks?: number;
  status?: string;
}

export interface CourseOut {
  id: string;
  title: string;
  description: string | null;
  learner_profile: LearnerProfile | null;
  effort_config: Record<string, number> | null;
  target_weeks: number | null;
  status: string;
  curriculum_id: string | null;
  created_at: string;
}

export interface ObjectiveCreate {
  text: string;
  bloom_level: BloomLevel;
  key_skills: string[];
  week_index?: number;
  order_index?: number;
}

export interface ObjectiveOut {
  id: string;
  draft_course_id: string;
  text: string;
  bloom_level: string;
  key_skills: string[];
  week_index: number | null;
  order_index: number;
}

export interface ItemCreate {
  title: string;
  kind?: string;
  content?: string;
  source_url?: string;
  metrics?: Record<string, unknown>;
  week_index?: number;
  order_index?: number;
}

export interface ItemOut {
  id: string;
  draft_course_id: string;
  kind: string;
  title: string;
  content: string | null;
  source_url: string | null;
  metrics: Record<string, unknown> | null;
  week_index: number | null;
  order_index: number;
  estimated_minutes: number | null;
}

export interface EffortWeek {
  student_minutes: number;
  item_count: number;
}

export interface EffortResponse {
  by_week: Record<string, EffortWeek>;
  total_student_minutes: number;
}

export interface OverloadWeek {
  week: number;
  student_hours: number;
  overload: boolean;
  new_concepts: number;
  density_warn: boolean;
}

export interface PublishResponse {
  curriculum_id: string;
  version_id: string;
  /** The initial-release ChangeRequest that gates activation (slice 5). */
  ccr_id: string;
  semver: string;
  /** Candidate lifecycle status ("review") — NOT active yet. */
  status: string;
  /** Always false on publish: the course is a review candidate, not live. */
  active: boolean;
  member_count: number;
  edge_count: number;
}

// ---------- full-course orchestrator (slice 4) ----------

export interface CourseBrief {
  title: string;
  topic: string;
  learner_profile?: LearnerProfile;
  target_weeks: number;
  /** 1..20 — the cost bound. */
  objectives_count: number;
  hours_per_week?: number;
}

/** 202 response — the generation job was scheduled; poll it for the result. */
export interface GenerationJobStarted {
  job_id: string;
}

export type GenerationJobState = "pending" | "running" | "complete" | "failed";

/** Poll response for a course-generation job. */
export interface GenerationJobStatus {
  job_id: string;
  status: GenerationJobState;
  completed_steps: number;
  total_steps: number;
  phase: string | null;
  /** Set only when status === "complete". */
  course_id: string | null;
  /** Set only when status === "failed". */
  error: string | null;
}

/**
 * Schedule a full-course generation from a brief (the heaviest AI op). Returns
 * immediately with a job id — the orchestration (1 + 2*objectives_count
 * sequential AI calls) runs in the background. Poll {@link getGenerationJob}
 * for progress and the resulting course id.
 */
export async function generateCourse(
  body: CourseBrief
): Promise<GenerationJobStarted> {
  const { data } = await apiClient.post<GenerationJobStarted>(
    "/builder/generate-course",
    body
  );
  return data;
}

/** Poll one course-generation job by id. */
export async function getGenerationJob(
  jobId: string
): Promise<GenerationJobStatus> {
  const { data } = await apiClient.get<GenerationJobStatus>(
    `/builder/generate-course/jobs/${jobId}`
  );
  return data;
}

// ---------- courses ----------

export async function createCourse(body: CourseCreate): Promise<CourseOut> {
  const { data } = await apiClient.post<CourseOut>("/builder/courses", body);
  return data;
}

export async function listCourses(): Promise<CourseOut[]> {
  const { data } = await apiClient.get<CourseOut[]>("/builder/courses");
  return data;
}

export async function getCourse(courseId: string): Promise<CourseOut> {
  const { data } = await apiClient.get<CourseOut>(
    `/builder/courses/${courseId}`
  );
  return data;
}

export async function updateCourse(
  courseId: string,
  body: CourseUpdate
): Promise<CourseOut> {
  const { data } = await apiClient.patch<CourseOut>(
    `/builder/courses/${courseId}`,
    body
  );
  return data;
}

// ---------- objectives ----------

export async function addObjective(
  courseId: string,
  body: ObjectiveCreate
): Promise<ObjectiveOut> {
  const { data } = await apiClient.post<ObjectiveOut>(
    `/builder/courses/${courseId}/objectives`,
    body
  );
  return data;
}

export async function listObjectives(
  courseId: string
): Promise<ObjectiveOut[]> {
  const { data } = await apiClient.get<ObjectiveOut[]>(
    `/builder/courses/${courseId}/objectives`
  );
  return data;
}

// ---------- items ----------

export async function addItem(
  courseId: string,
  body: ItemCreate
): Promise<ItemOut> {
  const { data } = await apiClient.post<ItemOut>(
    `/builder/courses/${courseId}/items`,
    body
  );
  return data;
}

export async function listItems(courseId: string): Promise<ItemOut[]> {
  const { data } = await apiClient.get<ItemOut[]>(
    `/builder/courses/${courseId}/items`
  );
  return data;
}

/** Align an item to an objective. */
export async function alignItem(
  itemId: string,
  objectiveId: string
): Promise<void> {
  await apiClient.post(`/builder/items/${itemId}/objectives`, {
    objective_id: objectiveId,
  });
}

// ---------- dependencies ----------

export async function addDependency(
  courseId: string,
  fromItemId: string,
  toItemId: string,
  edgeType: EdgeType = "prerequisite"
): Promise<unknown> {
  const { data } = await apiClient.post(
    `/builder/courses/${courseId}/dependencies`,
    { from_item_id: fromItemId, to_item_id: toItemId, edge_type: edgeType }
  );
  return data;
}

// ---------- item media attachments (slice 2) ----------

/** A media asset attached to a draft item (link + asset display fields). */
export interface ItemMedia {
  media_asset_id: string;
  order_index: number;
  kind: string;
  filename: string;
  mime: string;
  status: string;
  duration_s: number | null;
}

/** List an item's attached media assets, ordered by order_index. */
export async function listItemMedia(itemId: string): Promise<ItemMedia[]> {
  const { data } = await apiClient.get<ItemMedia[]>(
    `/builder/items/${itemId}/media`
  );
  return data;
}

/** Attach an owned (ready) media asset to a draft item. */
export async function attachMedia(
  itemId: string,
  mediaAssetId: string,
  orderIndex = 0
): Promise<ItemMedia> {
  const { data } = await apiClient.post<ItemMedia>(
    `/builder/items/${itemId}/media`,
    { media_asset_id: mediaAssetId, order_index: orderIndex }
  );
  return data;
}

/** Detach a media asset from a draft item (removes the link, not the asset). */
export async function detachMedia(
  itemId: string,
  assetId: string
): Promise<void> {
  await apiClient.delete(`/builder/items/${itemId}/media/${assetId}`);
}

// ---------- effort + overload ----------

export async function getEffort(courseId: string): Promise<EffortResponse> {
  const { data } = await apiClient.get<EffortResponse>(
    `/builder/courses/${courseId}/effort`
  );
  return data;
}

export async function getOverload(courseId: string): Promise<OverloadWeek[]> {
  const { data } = await apiClient.get<OverloadWeek[]>(
    `/builder/courses/${courseId}/overload`
  );
  return data;
}

// ---------- publish ----------

export async function publishCourse(
  courseId: string
): Promise<PublishResponse> {
  const { data } = await apiClient.post<PublishResponse>(
    `/builder/courses/${courseId}/publish`
  );
  return data;
}

// ---------- AI co-pilot (Phase 2) ----------

/**
 * Stateless AI categorization preview for an item — does NOT mutate the item.
 * Returns Claude's suggested kind/effort + a served-objective hint + rationale.
 * The deterministic auto-categorize already ran at create time; this is purely
 * an optional refinement the author can choose to Apply.
 */
export interface CategorizeResult {
  kind: string;
  served_objective_hint: string;
  estimated_minutes: number;
  complexity: number;
  rationale: string;
}

export type AdvisorNoteStatus = "accepted" | "dismissed";

/** A single advisory note from the AI co-pilot (suggestion / question / warning). */
export interface AdvisorNote {
  id: string;
  draft_course_id: string;
  target_kind: string | null;
  target_ref: string | null;
  kind: "suggestion" | "question" | "warning";
  text: string;
  status: string;
  created_at: string;
}

/** Result of inferring prerequisite dependencies across the draft's items. */
export interface InferDepsResult {
  suggested_created: number;
  missing_flagged: number;
}

/** Partial item update — used to APPLY an AI categorization, among other edits. */
export interface ItemUpdate {
  kind?: string;
  estimated_minutes?: number;
  title?: string;
  content?: string;
  source_url?: string;
  metrics?: Record<string, unknown>;
  week_index?: number;
  order_index?: number;
}

/** Stateless AI categorize preview for an item. 503 when AI is unconfigured. */
export async function categorizeItemAI(
  itemId: string
): Promise<CategorizeResult> {
  const { data } = await apiClient.post<CategorizeResult>(
    `/builder/items/${itemId}/categorize-ai`
  );
  return data;
}

/** Generate (and persist) advisory notes for a draft course. */
export async function advise(
  courseId: string,
  focus?: string
): Promise<AdvisorNote[]> {
  const { data } = await apiClient.post<AdvisorNote[]>(
    `/builder/courses/${courseId}/advise`,
    { focus }
  );
  return data;
}

/** List the persisted advisor notes for a draft course. */
export async function listAdvisorNotes(
  courseId: string
): Promise<AdvisorNote[]> {
  const { data } = await apiClient.get<AdvisorNote[]>(
    `/builder/courses/${courseId}/advisor-notes`
  );
  return data;
}

/** Accept or dismiss an advisor note. */
export async function updateAdvisorNote(
  noteId: string,
  status: AdvisorNoteStatus
): Promise<AdvisorNote> {
  const { data } = await apiClient.patch<AdvisorNote>(
    `/builder/advisor-notes/${noteId}`,
    { status }
  );
  return data;
}

/** Infer prerequisite dependencies across the draft's items. */
export async function inferDeps(courseId: string): Promise<InferDepsResult> {
  const { data } = await apiClient.post<InferDepsResult>(
    `/builder/courses/${courseId}/infer-deps`
  );
  return data;
}

/** Partial-update an item (e.g. apply an AI categorization). */
export async function updateItem(
  itemId: string,
  body: ItemUpdate
): Promise<ItemOut> {
  const { data } = await apiClient.patch<ItemOut>(
    `/builder/items/${itemId}`,
    body
  );
  return data;
}

// ---------- AI per-aspect generators (slice 3 — advisory drafts) ----------
//
// Mirror app/routers/authoring_ai.py + app/ai/schemas.py. Each returns an
// editable DRAFT the author reviews and ACCEPTS client-side — the handlers
// never write the draft into the model. All three 503 when the server has no
// ANTHROPIC_API_KEY (detect with `isAiNotConfigured`).

/** One AI-drafted, Bloom-tagged objective (GeneratedObjective). */
export interface GeneratedObjective {
  text: string;
  bloom_level: BloomLevel;
  key_skills: string[];
}

/** A set of AI-drafted objectives (GeneratedObjectives wrapper). */
export interface GeneratedObjectives {
  objectives: GeneratedObjective[];
}

/** AI-generated body for one draft item (GeneratedItemContent). */
export interface GeneratedItemContent {
  kind: string;
  content_markdown: string;
  summary: string;
  caveats: string[];
}

/** AI-generated assessment + rubric for one objective (GeneratedAssessment). */
export interface GeneratedAssessment {
  content_markdown: string;
  rubric: string;
  caveats: string[];
}

/**
 * Draft Bloom-tagged objectives for a course. ADVISORY — the returned drafts
 * are not written into the course; the author accepts them one at a time.
 * `topic` defaults to the course title/description on the server. 503 when AI
 * is unconfigured.
 */
export async function generateObjectives(
  courseId: string,
  body: { topic?: string; count?: number } = {}
): Promise<GeneratedObjectives> {
  const { data } = await apiClient.post<GeneratedObjectives>(
    `/builder/courses/${courseId}/generate-objectives`,
    body
  );
  return data;
}

/**
 * Draft the body of one item, grounded in its kind + linked objective(s).
 * ADVISORY — never auto-written into the item. 503 when AI is unconfigured.
 */
export async function generateItemContent(
  itemId: string
): Promise<GeneratedItemContent> {
  const { data } = await apiClient.post<GeneratedItemContent>(
    `/builder/items/${itemId}/generate-content`
  );
  return data;
}

/**
 * Draft an assessment + rubric for one objective. ADVISORY — never auto-written
 * into the draft. 503 when AI is unconfigured.
 */
export async function generateAssessment(
  objectiveId: string
): Promise<GeneratedAssessment> {
  const { data } = await apiClient.post<GeneratedAssessment>(
    `/builder/objectives/${objectiveId}/generate-assessment`
  );
  return data;
}

/**
 * True when an error is the backend's "AI not configured" signal (503 — the
 * `ANTHROPIC_API_KEY` is unset on the server). Dependency-light so components
 * don't each re-derive the axios error shape.
 */
export function isAiNotConfigured(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 503;
}
