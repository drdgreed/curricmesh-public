import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TutorChat } from "../pages/learn/TutorChat";
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

function renderChat(onSelectItem = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TutorChat enrollmentId="enr-1" items={ITEMS} onSelectItem={onSelectItem} />
    </QueryClientProvider>
  );
  return { onSelectItem };
}

describe("TutorChat", () => {
  beforeEach(() => {
    vi.mocked(askTutor).mockReset();
  });

  it("sends a question and renders the answer plus its citations", async () => {
    const user = userEvent.setup();
    vi.mocked(askTutor).mockResolvedValue({
      answer: "Agents plan, act, and observe in a loop.",
      conversation_id: "conv-1",
      citations: [
        { chunk_id: "chunk-1", source_member_id: "m-1", snippet: "the agent loop" },
      ],
    });
    renderChat();

    await user.type(screen.getByTestId("tutor-input"), "What is an agent loop?");
    await user.click(screen.getByTestId("tutor-send"));

    // The learner's own message renders immediately.
    expect(screen.getByText("What is an agent loop?")).toBeInTheDocument();

    await waitFor(() =>
      expect(
        screen.getByText("Agents plan, act, and observe in a loop.")
      ).toBeInTheDocument()
    );
    // Citation resolves to a human label for the source course item.
    const cites = screen.getByTestId("tutor-citations");
    expect(within(cites).getByText("Foundations · Week 1")).toBeInTheDocument();

    expect(askTutor).toHaveBeenCalledWith("enr-1", {
      question: "What is an agent loop?",
      conversationId: undefined,
      language: "en",
    });
  });

  it("renders a refusal answer as an ordinary tutor message", async () => {
    const user = userEvent.setup();
    vi.mocked(askTutor).mockResolvedValue({
      answer: "I don't have anything in this course to answer that.",
      conversation_id: "conv-1",
      citations: [],
    });
    renderChat();

    await user.type(screen.getByTestId("tutor-input"), "Who won the game?");
    await user.click(screen.getByTestId("tutor-send"));

    await waitFor(() =>
      expect(
        screen.getByText("I don't have anything in this course to answer that.")
      ).toBeInTheDocument()
    );
    // Refusal still renders as a normal tutor message; no citation block.
    expect(screen.getByTestId("tutor-msg-tutor")).toBeInTheDocument();
    expect(screen.queryByTestId("tutor-citations")).not.toBeInTheDocument();
  });

  it("threads the conversation_id on the second turn", async () => {
    const user = userEvent.setup();
    vi.mocked(askTutor)
      .mockResolvedValueOnce({
        answer: "First answer.",
        conversation_id: "conv-42",
        citations: [],
      })
      .mockResolvedValueOnce({
        answer: "Second answer.",
        conversation_id: "conv-42",
        citations: [],
      });
    renderChat();

    await user.type(screen.getByTestId("tutor-input"), "First question");
    await user.click(screen.getByTestId("tutor-send"));
    await waitFor(() =>
      expect(screen.getByText("First answer.")).toBeInTheDocument()
    );

    await user.type(screen.getByTestId("tutor-input"), "Second question");
    await user.click(screen.getByTestId("tutor-send"));
    await waitFor(() =>
      expect(screen.getByText("Second answer.")).toBeInTheDocument()
    );

    // First call has no conversation id; the second reuses the returned one.
    expect(vi.mocked(askTutor).mock.calls[0][1]).toEqual({
      question: "First question",
      conversationId: undefined,
      language: "en",
    });
    expect(vi.mocked(askTutor).mock.calls[1][1]).toEqual({
      question: "Second question",
      conversationId: "conv-42",
      language: "en",
    });
  });

  it("jumps to the source item when a citation chip is clicked", async () => {
    const user = userEvent.setup();
    vi.mocked(askTutor).mockResolvedValue({
      answer: "See the foundations lesson.",
      conversation_id: "conv-1",
      citations: [
        { chunk_id: "chunk-1", source_member_id: "m-1", snippet: "foundations" },
      ],
    });
    const { onSelectItem } = renderChat();

    await user.type(screen.getByTestId("tutor-input"), "Where do I start?");
    await user.click(screen.getByTestId("tutor-send"));
    const chip = await screen.findByTestId("tutor-citation");
    await user.click(chip);

    expect(onSelectItem).toHaveBeenCalledWith("m-1");
  });
});
