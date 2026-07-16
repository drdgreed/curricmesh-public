/**
 * Learner-delivery API client (Phase 2 Course Player). Mirrors the backend
 * contract under ``/api/v1/learn`` (app/routers/learn.py) — response models
 * ``CatalogEntry`` / ``EnrollmentOut`` / ``CourseStructure`` / ``CourseItem`` /
 * ``ProgressOut`` / ``SubmissionOut``. Reuses the shared axios `apiClient`
 * whose baseURL is `.../api/v1`, so paths here are prefixed with `/learn`.
 *
 * All endpoints are learner-role gated and enrollment-scoped: a learner only
 * ever sees their own tenant's released courses and their own enrollments.
 *
 * verbatimModuleSyntax is on → the type surface is exported with `export type`
 * and consumers must `import type { … }`.
 */

import { apiClient } from "./client";

// ---------- response types (mirror the pydantic models) ----------

export type ProgressStatus = "not_started" | "in_progress" | "complete";

/** A released course available to enroll in (this tenant). */
export interface CatalogEntry {
  curriculum_version_id: string;
  curriculum_id: string;
  title: string;
  version: string; // "major.minor.patch"
}

/** The calling learner's enrollment + progress summary. */
export interface EnrollmentOut {
  id: string;
  curriculum_version_id: string;
  learner_id: string;
  status: string; // active | completed | withdrawn
  title: string;
  completed_items: number;
  total_items: number;
  enrolled_at: string;
  completed_at: string | null;
}

/** A fresh presigned media reference frozen on the pinned version. */
export interface MediaRef {
  id: string | null;
  kind: string | null;
  filename: string | null;
  url: string; // fresh presigned GET
}

/** One item within a pinned course version. */
export interface CourseItem {
  member_id: string;
  section: string;
  week_index: number;
  order: number;
  kind: string;
  lineage_key: string;
  content: string;
  media: MediaRef[];
  progress_status: string;
}

/** The pinned course structure — items ordered by week/order + presigned media. */
export interface CourseStructure {
  enrollment_id: string;
  curriculum_version_id: string;
  title: string;
  status: string;
  completed_items: number;
  total_items: number;
  items: CourseItem[];
}

/** The result of marking an item's progress (recomputes course completion). */
export interface ProgressOut {
  member_id: string;
  status: string;
  enrollment_status: string;
  completed_items: number;
  total_items: number;
}

/** A learner's submitted response to an assessment item. */
export interface SubmissionOut {
  id: string;
  enrollment_id: string;
  content_member_id: string;
  submitted_at: string;
  score: number | null;
  feedback: string | null;
}

/** A rendered slide deck for the pinned version, with fresh presigned URLs. */
export interface DeckOut {
  id: string;
  source_member_id: string | null;
  status: string;
  created_at: string;
  html_url: string; // fresh presigned GET — embed the slides
  pdf_url: string; // fresh presigned GET — download
  pptx_url: string; // fresh presigned GET — download
}

// ---------- typed API functions ----------

/** Released courses this tenant offers (source of truth: active content version). */
export async function getCatalog(): Promise<CatalogEntry[]> {
  const { data } = await apiClient.get<CatalogEntry[]>("/learn/catalog");
  return data;
}

/** Self-enroll into a released course (pins the exact version). 409 if already enrolled. */
export async function enroll(
  curriculumVersionId: string
): Promise<EnrollmentOut> {
  const { data } = await apiClient.post<EnrollmentOut>("/learn/enroll", {
    curriculum_version_id: curriculumVersionId,
  });
  return data;
}

/** The calling learner's courses + per-course progress. */
export async function getEnrollments(): Promise<EnrollmentOut[]> {
  const { data } = await apiClient.get<EnrollmentOut[]>("/learn/enrollments");
  return data;
}

/** The pinned structure for an enrollment (items + presigned media). */
export async function getCourse(
  enrollmentId: string
): Promise<CourseStructure> {
  const { data } = await apiClient.get<CourseStructure>(
    `/learn/courses/${enrollmentId}`
  );
  return data;
}

/** The rendered slide decks for an enrolled course (fresh presigned URLs). */
export async function getCourseDecks(
  enrollmentId: string
): Promise<DeckOut[]> {
  const { data } = await apiClient.get<DeckOut[]>(
    `/learn/courses/${enrollmentId}/decks`
  );
  return data;
}

/** A single item (content + presigned media + my progress). */
export async function getItem(
  enrollmentId: string,
  memberId: string
): Promise<CourseItem> {
  const { data } = await apiClient.get<CourseItem>(
    `/learn/items/${enrollmentId}/${memberId}`
  );
  return data;
}

/** Mark an item not_started / in_progress / complete; recomputes completion. */
export async function setProgress(
  enrollmentId: string,
  memberId: string,
  status: ProgressStatus
): Promise<ProgressOut> {
  const { data } = await apiClient.post<ProgressOut>(
    `/learn/progress/${enrollmentId}/${memberId}`,
    { status }
  );
  return data;
}

/** Submit a learner's response to an assessment item. */
export async function submitAssessment(
  enrollmentId: string,
  memberId: string,
  responseText: string
): Promise<SubmissionOut> {
  const { data } = await apiClient.post<SubmissionOut>(
    `/learn/submit/${enrollmentId}/${memberId}`,
    { response_text: responseText }
  );
  return data;
}
