import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TutorChat } from "../pages/learn/TutorChat";
import { LanguageSelector } from "../i18n/LanguageSelector";
import { SessionLanguageProvider } from "../i18n/SessionLanguageContext";
import type { CourseItem } from "../api/learn";

vi.mock("../api/tutor", () => ({
  askTutor: vi.fn(),
}));

import { askTutor } from "../api/tutor";

const ITEMS: CourseItem[] = [
  {
    member_id: "m-1",
    section: "Foundations",
    week_index: 1,
    order: 0,
    kind: "lesson_plan",
    lineage_key: "agentic-ai/v1/01/lesson_plan",
    content: "# Intro",
    media: [],
    progress_status: "complete",
  },
];

/** Render the session-language selector alongside the tutor chat, both under one
 * SessionLanguageProvider — the real wiring: selecting a language must flow
 * through the session context into every tutor request. */
function renderWithSelector() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SessionLanguageProvider>
        <LanguageSelector />
        <TutorChat enrollmentId="enr-1" items={ITEMS} />
      </SessionLanguageProvider>
    </QueryClientProvider>
  );
}

describe("Session language → tutor requests", () => {
  beforeEach(() => {
    vi.mocked(askTutor).mockReset();
    vi.mocked(askTutor).mockResolvedValue({
      answer: "respuesta",
      conversation_id: "conv-1",
      citations: [],
    });
    try {
      sessionStorage.clear();
    } catch {
      /* ignore */
    }
  });

  it("defaults to English when the learner has not chosen a language", async () => {
    const user = userEvent.setup();
    renderWithSelector();

    await user.type(screen.getByTestId("tutor-input"), "hola");
    await user.click(screen.getByTestId("tutor-send"));

    await waitFor(() => expect(askTutor).toHaveBeenCalled());
    expect(vi.mocked(askTutor).mock.calls[0][1]).toEqual({
      question: "hola",
      conversationId: undefined,
      language: "en",
    });
  });

  it("sends the chosen language on the tutor request after the learner picks one", async () => {
    const user = userEvent.setup();
    renderWithSelector();

    // Pick Spanish from the session-language selector (MUI select).
    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Español" }));

    await user.type(screen.getByTestId("tutor-input"), "que es un agente");
    await user.click(screen.getByTestId("tutor-send"));

    await waitFor(() => expect(askTutor).toHaveBeenCalled());
    expect(vi.mocked(askTutor).mock.calls[0][1]).toEqual({
      question: "que es un agente",
      conversationId: undefined,
      language: "Spanish",
    });
  });
});
