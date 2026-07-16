import axios from "axios";

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "http://localhost:8000/api/v1",
});

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem("auth_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On any 401 (expired / invalid token), clear the session and bounce to the
// login screen — otherwise an expired token leaves every page stuck on a
// "failed to load" error with no way to recover.
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      for (const k of ["auth_token", "auth_role", "auth_org", "auth_org_name"]) {
        localStorage.removeItem(k);
      }
      if (window.location.pathname !== "/login") {
        window.location.assign("/login");
      }
    }
    return Promise.reject(error);
  }
);

// ---------- types ----------

export interface LoginResponse {
  access_token: string;
  token_type: string;
}

export interface MeResponse {
  sub: string;
  role: string;
  org: string | null;
  org_name: string | null;
}

export interface VersionSummary {
  id: string;
  semver: string;
  status: string;
  created_at: string;
}

export interface CohortSummary {
  id: string;
  name: string;
  version_id: string | null;
  start_date: string | null;
  end_date: string | null;
}

export interface MisalignmentEntry {
  dependent_asset_id: string;
  dependency_asset_id: string;
  dependent_asset_name: string;
  dependency_asset_name: string;
  dependent_updated_at: string | null;
  dependency_updated_at: string | null;
  reason: string;
}

export interface CurriculumSummary {
  id: string;
  name: string;
  slug: string;
  current_version_id: string | null;
  versions: VersionSummary[];
  cohorts: CohortSummary[];
  alignment: MisalignmentEntry[];
}

export interface RecentEvent {
  id: string;
  event_type: string;
  target: string | null;
  actor_id: string | null;
  actor_label: string | null;
  target_label: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface DashboardResponse {
  curricula: CurriculumSummary[];
  recent_events: RecentEvent[];
}

// ---------- typed API functions ----------

export async function login(
  email: string,
  password: string
): Promise<LoginResponse> {
  const { data } = await apiClient.post<LoginResponse>("/auth/login", {
    email,
    password,
  });
  return data;
}

export async function getMe(): Promise<MeResponse> {
  const { data } = await apiClient.get<MeResponse>("/auth/me");
  return data;
}

export async function getDashboard(): Promise<DashboardResponse> {
  const { data } = await apiClient.get<DashboardResponse>("/dashboard");
  return data;
}

// ---------- AI-spend usage (internal, architect/program_manager only) ----------

export interface AiUsagePersisted {
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  by_model: Record<
    string,
    { calls: number; input_tokens: number; output_tokens: number; cost_usd: number }
  >;
  by_day: { date: string; calls: number; cost_usd: number }[];
}

export interface AiUsageSummary {
  total_calls: number;
  total_cost_usd: number;
  persisted: AiUsagePersisted;
  [k: string]: unknown;
}

export async function getAiUsage(): Promise<AiUsageSummary> {
  const { data } = await apiClient.get<AiUsageSummary>("/internal/ai-usage");
  return data;
}

// ---------- graph types ----------

export interface GraphNode {
  id: string;
  kind: string;
  label: string;
  latest_version: string | null;
  status: string | null;
}

export interface GraphEdge {
  from_asset_id: string;
  to_asset_id: string;
  edge_type: string;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  misaligned_asset_ids: string[];
}

export async function getGraph(curriculumId: string): Promise<GraphResponse> {
  const { data } = await apiClient.get<GraphResponse>(
    `/curricula/${curriculumId}/graph`
  );
  return data;
}

// ---------- asset versions + diff types (Task B5) ----------

export interface AssetVersionItem {
  id: string;
  semver: string;
  status: string;
  created_at: string;
}

export interface TextDiff {
  added: string[];
  removed: string[];
  unified: string;
}

export interface ChangedEntry {
  key: string;
  from: unknown;
  to: unknown;
}

export interface StructuredDiff {
  added: unknown[];
  removed: unknown[];
  changed: ChangedEntry[];
}

export interface DiffResult {
  kind: string;
  text: TextDiff | null;
  structured: StructuredDiff | null;
}

export async function listAssetVersions(assetId: string): Promise<AssetVersionItem[]> {
  const { data } = await apiClient.get<AssetVersionItem[]>(
    `/assets/${assetId}/versions`
  );
  return data;
}

export async function getDiff(
  assetId: string,
  from: string,
  to: string
): Promise<DiffResult> {
  const { data } = await apiClient.get<DiffResult>(`/assets/${assetId}/diff`, {
    params: { from, to },
  });
  return data;
}

// ---------- AI-findings inbox (Task C5) ----------

export interface AICCRDraft {
  id: string;
  curriculum_id: string;
  title: string;
  rationale: string | null;
  proposed_bump: string | null;
  status: string;
  impact: Record<string, any> | null;
  external_link: string | null;
  author_id: string | null;
  created_at: string;
}

export interface AIDraftQA {
  id: string;
  ccr_id: string;
  ccr_title: string | null;
  dimension_scores: Record<string, number> | null;
  evidence: Record<string, string> | null;
  created_at: string;
}

export interface AIInboxResponse {
  drafted_ccrs: AICCRDraft[];
  draft_qa_reviews: AIDraftQA[];
}

export async function getAIInbox(): Promise<AIInboxResponse> {
  const { data } = await apiClient.get<AIInboxResponse>("/ai/inbox");
  return data;
}

export async function submitQAReview(
  ccrId: string,
  dimensionScores: Record<string, number>,
  verdict: "pass" | "fail"
): Promise<unknown> {
  const { data } = await apiClient.post(`/ccrs/${ccrId}/qa`, {
    dimension_scores: dimensionScores,
    verdict,
  });
  return data;
}

/**
 * The six QA dimensions the backend requires for a passing review. All six must
 * be scored 1–5 or the QA endpoint returns 400.
 */
export const QA_DIMENSIONS = [
  "content_accuracy",
  "alignment",
  "prerequisites",
  "consistency",
  "instructor_support",
  "student_experience",
] as const;

/**
 * Submit a passing QA review with every dimension scored 5. Convenience wrapper
 * for the Review page's "Mark QA passed" action — the loop just needs a pass on
 * the gate, not a granular per-dimension form.
 */
export async function markCCRQaPassed(ccrId: string): Promise<unknown> {
  const scores = Object.fromEntries(QA_DIMENSIONS.map((d) => [d, 5]));
  return submitQAReview(ccrId, scores, "pass");
}

// ---------- CCR create (Part B) ----------

export const ASSET_KINDS = [
  "lesson_plan",
  "slides",
  "assessment",
  "rubric",
  "lab",
  "spec",
  "starter",
  "references",
  "learning_objectives",
  "project",
] as const;

export type AssetKind = (typeof ASSET_KINDS)[number];

/**
 * Friendly display labels for asset kinds. Mirrors the backend
 * `KIND_LABELS` (app/core/naming.py) — single source of truth lives in the
 * backend; this is the frontend copy used for form option labels.
 */
export const ASSET_KIND_LABELS: Record<AssetKind, string> = {
  lab: "Coding Lab",
  lesson_plan: "Lesson Plan",
  learning_objectives: "Learning Objectives",
  references: "References",
  starter: "Starter Code",
  project: "Project",
  slides: "Slides",
  assessment: "Assessment",
  rubric: "Rubric",
  spec: "Spec",
};

export type BumpType = "major" | "minor" | "patch";

export interface CCRCreate {
  curriculum_id: string;
  title: string;
  rationale?: string;
  proposed_bump: BumpType;
  affected_kinds: AssetKind[];
  instructor_override?: boolean;
  target_version_id?: string;
  affected_asset_ids?: string[];
  external_link?: string;
  // Structured executable change-set for PR-style review → merge. When present,
  // POST /ccrs/{id}/merge replays it through fork() once the CCR is approved.
  change_set?: ReleaseChangeSet;
}

export interface CCROut {
  id: string;
  curriculum_id: string;
  author_id: string | null;
  title: string;
  rationale: string | null;
  proposed_bump: string | null;
  external_link: string | null;
  impact: Record<string, unknown> | null;
  // The structured executable change-set, or null for description-only CCRs.
  change_set: ReleaseChangeSet | null;
  status: string;
  created_at: string;
}

export async function createCCR(body: CCRCreate): Promise<CCROut> {
  const { data } = await apiClient.post<CCROut>("/ccrs", body);
  return data;
}

/** List change requests, optionally filtered by status. */
export async function listCCRs(status?: string): Promise<CCROut[]> {
  const { data } = await apiClient.get<CCROut[]>("/ccrs", {
    params: status ? { status } : undefined,
  });
  return data;
}

/** Run AI enrichment (placement + draft frame) on a gap CCR; returns the updated CCR. */
export async function enrichCCR(ccrId: string): Promise<CCROut> {
  const { data } = await apiClient.post<CCROut>(`/ccrs/${ccrId}/enrich`);
  return data;
}

export type ApprovalDecision = "approve" | "reject";

/** Record an approval/rejection on a CCR. */
export async function approveCCR(
  ccrId: string,
  decision: ApprovalDecision
): Promise<unknown> {
  const { data } = await apiClient.post(`/ccrs/${ccrId}/approvals`, {
    decision,
  });
  return data;
}

/** Merge an approved CCR — replays its change-set through fork(). */
export async function mergeCCR(ccrId: string): Promise<ReleaseResponse> {
  const { data } = await apiClient.post<ReleaseResponse>(
    `/ccrs/${ccrId}/merge`
  );
  return data;
}

/**
 * Execute the release gate on a CCR — POST /ccrs/{id}/release. For an
 * initial-release CCR (an authored course's first publish) this activates the
 * pre-active candidate CurriculumVersion once the QA + approval gate clears;
 * returns the now-terminal CCR. WorkflowError (gate unmet) → 400.
 */
export async function releaseCCR(ccrId: string): Promise<CCROut> {
  const { data } = await apiClient.post<CCROut>(`/ccrs/${ccrId}/release`);
  return data;
}

/**
 * The release-gate status for a CCR — what the merge gate still needs. The gate
 * (`can_release`) requires: a passing QA review + ≥2 approvals + ≥1 approval
 * from an instructor role. The component flags drive the Review checklist.
 */
export interface ReleaseGate {
  has_change_set: boolean;
  qa_passed: boolean;
  approval_count: number;
  has_instructor_approval: boolean;
  can_release: boolean;
}

/** Fetch the merge-gate status for a CCR. */
export async function getCCRGate(ccrId: string): Promise<ReleaseGate> {
  const { data } = await apiClient.get<ReleaseGate>(`/ccrs/${ccrId}/gate`);
  return data;
}

// ---------- analytics (V3-A: change-velocity & time-in-state) ----------

export interface VelocityBucket {
  bucket_start: string;
  ccrs_opened: number;
  versions_released: number;
}

export interface StateDuration {
  state: string;
  n: number;
  mean_days: number | null;
  median_days: number | null;
}

export interface CadenceSummary {
  releases: number;
  mean_days_between: number | null;
  median_days_between: number | null;
}

export interface StateCount {
  entity: string;
  status: string;
  count: number;
}

export interface AnalyticsOverview {
  velocity: VelocityBucket[];
  time_in_state: StateDuration[];
  cadence: CadenceSummary;
  distribution: StateCount[];
}

export async function getAnalyticsOverview(): Promise<AnalyticsOverview> {
  const { data } = await apiClient.get<AnalyticsOverview>("/analytics/overview");
  return data;
}

// ---------- Feature A: course content browser ----------

export interface CalendarTile {
  id: string; // legacy Asset.id — navigable (matches graph nodes)
  lineage_key: string;
  kind: string;
  label: string;
  source_url: string | null;
  latest_version: string | null;
  status: string | null;
  misaligned: boolean;
}

export interface CalendarSection {
  week_index: number;
  section: string;
  tiles: CalendarTile[];
}

export interface CourseCalendarResponse {
  curriculum_id: string;
  sections: CalendarSection[];
}

export interface AssetVersionRef {
  seq: number;
  content_hash: string;
  created_at: string;
}

export interface AssetEdgeRef {
  id: string;
  lineage_key: string;
  label: string;
  edge_type: string;
}

export interface AssetDetailResponse {
  id: string;
  lineage_key: string;
  kind: string;
  label: string;
  source_url: string | null;
  content: string | null;
  content_metadata: Record<string, unknown> | null;
  content_seq: number | null;
  content_hash: string | null;
  version_history: AssetVersionRef[];
  prerequisites: AssetEdgeRef[];
  dependents: AssetEdgeRef[];
}

export async function getCourseCalendar(
  curriculumId: string
): Promise<CourseCalendarResponse> {
  const { data } = await apiClient.get<CourseCalendarResponse>(
    `/curricula/${curriculumId}/calendar`
  );
  return data;
}

export async function getAssetDetail(
  assetId: string
): Promise<AssetDetailResponse> {
  const { data } = await apiClient.get<AssetDetailResponse>(
    `/assets/${assetId}`
  );
  return data;
}

export async function setAssetSourceUrl(
  assetId: string,
  sourceUrl: string | null
): Promise<{ id: string; lineage_key: string; source_url: string | null }> {
  const { data } = await apiClient.patch(`/assets/${assetId}/source-url`, {
    source_url: sourceUrl,
  });
  return data;
}

// ---------- Feature B: rich change authoring → release ----------

export interface ReleaseChangedItem {
  lineage_key: string;
  content?: string | null;
  metadata?: Record<string, unknown> | null;
  section?: string | null;
  week_index?: number | null;
  order?: number | null;
}

export interface ReleaseAddedItem {
  lineage_key: string;
  kind: AssetKind;
  content?: string | null;
  metadata?: Record<string, unknown> | null;
  section?: string;
  week_index?: number;
  order?: number;
  source_url?: string | null;
}

export interface ReleaseEdge {
  from_key: string;
  to_key: string;
  edge_type?: string;
  validated_against_seq?: number | null;
}

/**
 * The structured executable change-set persisted on a CCR — the `ReleaseCreate`
 * shape minus the transport-only fields (expected_active_id / ccr_id / note).
 * Carried on `CCRCreate.change_set` / `CCROut.change_set` for PR-style merge.
 */
export interface ReleaseChangeSet {
  bump: BumpType;
  changed: ReleaseChangedItem[];
  added: ReleaseAddedItem[];
  removed: string[];
  edges_added: ReleaseEdge[];
  edges_removed: ReleaseEdge[];
}

export interface ReleaseCreate {
  bump: BumpType;
  changed: ReleaseChangedItem[];
  added: ReleaseAddedItem[];
  removed: string[];
  edges_added: ReleaseEdge[];
  edges_removed: ReleaseEdge[];
  expected_active_id?: string | null;
  ccr_id?: string | null;
  note?: string | null;
}

export interface ReleaseSummary {
  changed: number;
  added: number;
  removed: number;
  edges_added: number;
  edges_removed: number;
}

export interface ReleaseResponse {
  curriculum_id: string;
  version_id: string;
  semver: string;
  status: string;
  parent_version_id: string | null;
  member_count: number;
  edge_count: number;
  summary: ReleaseSummary;
}

export async function createRelease(
  curriculumId: string,
  body: ReleaseCreate
): Promise<ReleaseResponse> {
  const { data } = await apiClient.post<ReleaseResponse>(
    `/curricula/${curriculumId}/releases`,
    body
  );
  return data;
}

// ---------- AI CCR-impact guidance (Propose Change preview) ----------

export type CognitiveLoad = "lower" | "unchanged" | "higher" | "much_higher";

/**
 * Stateless impact-preview request. `change_set` is the SAME shape the
 * authoring form already assembles for release/submit — reuse it directly.
 * Omit `ccr_id` for a stateless preview (nothing is persisted).
 */
export interface ImpactRequest {
  change_set: ReleaseChangeSet;
  title?: string;
  rationale?: string;
  ccr_id?: string;
}

/** Claude's advisory estimate of a staged change-set's curricular impact. */
export interface ImpactReport {
  summary: string;
  learning_objectives_impact: string;
  affected_objectives: string[];
  duration_delta_minutes: number;
  duration_rationale: string;
  cognitive_load: CognitiveLoad;
  cognitive_load_rationale: string;
  risks: string[];
  recommendations: string[];
}

export async function analyzeImpact(
  curriculumId: string,
  body: ImpactRequest
): Promise<ImpactReport> {
  const { data } = await apiClient.post<ImpactReport>(
    `/curricula/${curriculumId}/impact`,
    body
  );
  return data;
}

// ---------- precise staleness: per-curriculum alignment ----------

/**
 * One out-of-alignment dependency. Ids are legacy Asset ids (same ids the
 * graph / course tiles use). `mode="revision"` → the prerequisite advanced
 * `revision_delta` content-revisions since this dependency was validated;
 * `mode="timestamp"` → the legacy "prerequisite edited more recently" heuristic
 * (no precise delta, so `revision_delta` is null).
 */
export interface AlignmentItem {
  dependent_id: string;
  dependent_label: string;
  prerequisite_id: string;
  prerequisite_label: string;
  mode: "revision" | "timestamp";
  revision_delta: number | null;
}

export interface AlignmentResponse {
  items: AlignmentItem[];
}

export async function getAlignment(
  curriculumId: string
): Promise<AlignmentResponse> {
  const { data } = await apiClient.get<AlignmentResponse>(
    `/curricula/${curriculumId}/alignment`
  );
  return data;
}

// ---------- release diff: version-to-version structural delta ----------

/**
 * The curriculum's active `CurriculumVersion` id + its parent. Drives the
 * default "what changed in the current version" diff — the diff endpoint keys
 * on `CurriculumVersion` ids, which the legacy `/versions` list does not expose.
 */
export interface ActiveVersion {
  curriculum_id: string;
  head_version_id: string;
  parent_version_id: string | null;
  semver: string;
  status: string;
}

export async function getActiveVersion(
  curriculumId: string
): Promise<ActiveVersion> {
  const { data } = await apiClient.get<ActiveVersion>(
    `/curricula/${curriculumId}/active-version`
  );
  return data;
}

export interface VersionDiffAsset {
  asset_id: string;
  label: string;
  seq: number;
  content_hash: string;
}

export interface VersionDiffChanged {
  asset_id: string;
  label: string;
  from_seq: number;
  from_hash: string;
  to_seq: number;
  to_hash: string;
}

export interface VersionDiffEdge {
  from_label: string;
  to_label: string;
  edge_type: string;
}

export interface VersionDiffResult {
  base_version_id: string | null;
  head_version_id: string;
  assets_added: VersionDiffAsset[];
  assets_removed: VersionDiffAsset[];
  assets_changed: VersionDiffChanged[];
  edges_added: VersionDiffEdge[];
  edges_removed: VersionDiffEdge[];
}

export async function getVersionDiff(
  curriculumId: string,
  headVersionId: string,
  baseVersionId?: string
): Promise<VersionDiffResult> {
  const { data } = await apiClient.get<VersionDiffResult>(
    `/curricula/${curriculumId}/versions/${headVersionId}/diff`,
    { params: baseVersionId ? { base: baseVersionId } : undefined }
  );
  return data;
}

// ---------------------------------------------------------------------------
// Freshness pipeline — Monitor Queue (Phase 2 Judge)
// ---------------------------------------------------------------------------

export interface AssessmentOut {
  id: string;
  curriculum_id: string;
  topic: string;
  display_topic: string;
  recommendation: "adopt_now" | "monitor" | "reject";
  confidence: number;
  scores: Record<string, unknown>;
  rationale: string;
  dossier: Array<{
    run_date: string;
    source_kinds: string[];
    evidence: string[];
    [key: string]: unknown;
  }>;
  times_seen: number;
  times_seen_at_last_eval: number;
  promoted_ccr_id: string | null;
  first_seen_at: string;
  last_evaluated_at: string;
}

/** List gap assessments, optionally filtered by recommendation. */
export async function listAssessments(
  recommendation?: string
): Promise<AssessmentOut[]> {
  const { data } = await apiClient.get<AssessmentOut[]>(
    "/freshness/assessments",
    { params: recommendation ? { recommendation } : undefined }
  );
  return data;
}
