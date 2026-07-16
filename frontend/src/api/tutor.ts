/**
 * RAG tutor API client (Phase B, B6) — learner-facing, enrollment-scoped.
 * Mirrors the backend contract under ``/api/v1/learn/tutor`` (app/routers/tutor.py):
 *   POST /learn/tutor/{enrollment_id}/ask
 *       {question, conversation_id?} -> {answer, citations, conversation_id}
 *   GET  /learn/tutor/{enrollment_id}/conversations/{conversation_id}
 *       -> the full server-side conversation record.
 *
 * The tutor grounds every answer in the enrolled version's content and refuses
 * (returns a refusal string in ``answer``) when it has no supporting context.
 * PII is redacted server-side, so the UI sends the learner's raw question.
 *
 * Reuses the shared axios `apiClient` whose baseURL is `.../api/v1`, so paths
 * here are prefixed with `/learn/tutor`. verbatimModuleSyntax is on → the type
 * surface is exported with `export type` and consumers must `import type { … }`.
 */

import { apiClient } from "./client";

// ---------- response types (mirror the pydantic models) ----------

/** One grounding reference behind a tutor answer (CitationOut). */
export interface TutorCitation {
  chunk_id: string;
  /** The course item (member) the snippet came from, when resolvable. */
  source_member_id: string | null;
  snippet: string;
}

/** The tutor's reply to a single question (AskResponse). */
export interface TutorAnswer {
  answer: string;
  citations: TutorCitation[];
  conversation_id: string;
}

/** A persisted turn in a tutor conversation (MessageOut). */
export interface TutorMessage {
  id: string;
  role: string; // "learner" | "tutor"
  text: string;
  citations: TutorCitation[] | null;
  created_at: string;
}

/** The full server-side record of a tutor conversation (ConversationOut). */
export interface TutorConversation {
  conversation_id: string;
  enrollment_id: string;
  messages: TutorMessage[];
}

// ---------- typed API functions ----------

/**
 * Ask the tutor a question, grounded in the enrolled version's content.
 * Pass ``conversationId`` on follow-up turns to keep conversation continuity.
 * ``language`` (T3b) is the learner's session-chosen reply language; it defaults
 * to English server-side when omitted.
 */
export async function askTutor(
  enrollmentId: string,
  args: { question: string; conversationId?: string; language?: string }
): Promise<TutorAnswer> {
  const { data } = await apiClient.post<TutorAnswer>(
    `/learn/tutor/${enrollmentId}/ask`,
    {
      question: args.question,
      conversation_id: args.conversationId ?? null,
      language: args.language ?? "en",
    }
  );
  return data;
}

/** The full server-side record of a tutor conversation. */
export async function getTutorConversation(
  enrollmentId: string,
  conversationId: string
): Promise<TutorConversation> {
  const { data } = await apiClient.get<TutorConversation>(
    `/learn/tutor/${enrollmentId}/conversations/${conversationId}`
  );
  return data;
}
