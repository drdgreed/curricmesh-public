import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock the builder API. isAiNotConfigured keeps its real 503-detection impl so
// the panel's graceful-degradation branch is exercised faithfully.
vi.mock("../api/builder", () => ({
  listAdvisorNotes: vi.fn(),
  advise: vi.fn(),
  updateAdvisorNote: vi.fn(),
  inferDeps: vi.fn(),
  isAiNotConfigured: (e: unknown) =>
    (e as { response?: { status?: number } })?.response?.status === 503,
}));

import { CopilotPanel } from "../pages/builder/CopilotPanel";
import type { AdvisorNote, CourseOut } from "../api/builder";
import { advise, inferDeps, listAdvisorNotes, updateAdvisorNote } from "../api/builder";

const course: CourseOut = {
  id: "course-1",
  title: "Test Course",
  description: null,
  learner_profile: null,
  effort_config: null,
  target_weeks: null,
  status: "draft",
  curriculum_id: null,
  created_at: "2026-01-01T00:00:00Z",
};

function makeNote(over: Partial<AdvisorNote>): AdvisorNote {
  return {
    id: "n1",
    draft_course_id: "course-1",
    target_kind: null,
    target_ref: null,
    kind: "suggestion",
    text: "A suggestion",
    status: "open",
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <CopilotPanel course={course} />
    </QueryClientProvider>
  );
}

describe("CopilotPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listAdvisorNotes).mockResolvedValue([]);
    vi.mocked(advise).mockResolvedValue([]);
    vi.mocked(inferDeps).mockResolvedValue({
      suggested_created: 0,
      missing_flagged: 0,
    });
    vi.mocked(updateAdvisorNote).mockResolvedValue(
      makeNote({ status: "accepted" })
    );
  });

  it("renders the panel and both action buttons", async () => {
    renderPanel();
    expect(await screen.findByTestId("copilot-panel")).toBeInTheDocument();
    expect(screen.getByTestId("copilot-advise-btn")).toBeInTheDocument();
    expect(screen.getByTestId("copilot-infer-btn")).toBeInTheDocument();
  });

  it("Get AI guidance calls advise and renders the returned notes", async () => {
    vi.mocked(advise).mockResolvedValue([]);
    // After advise succeeds the notes query refetches and returns 2 notes.
    vi.mocked(listAdvisorNotes)
      .mockResolvedValueOnce([])
      .mockResolvedValue([
        makeNote({ id: "n1", kind: "suggestion", text: "Add a lab" }),
        makeNote({ id: "n2", kind: "warning", text: "Week 3 is overloaded" }),
      ]);

    renderPanel();
    fireEvent.click(await screen.findByTestId("copilot-advise-btn"));

    await waitFor(() => expect(advise).toHaveBeenCalledWith("course-1"));
    await waitFor(() => {
      expect(screen.getAllByTestId("advisor-note")).toHaveLength(2);
    });
    expect(screen.getAllByTestId("advisor-note-accept")).toHaveLength(2);
    expect(screen.getAllByTestId("advisor-note-dismiss")).toHaveLength(2);
  });

  it("Accept calls updateAdvisorNote with 'accepted'", async () => {
    vi.mocked(listAdvisorNotes).mockResolvedValue([
      makeNote({ id: "n1", text: "Add a lab" }),
    ]);

    renderPanel();
    fireEvent.click(await screen.findByTestId("advisor-note-accept"));

    await waitFor(() =>
      expect(updateAdvisorNote).toHaveBeenCalledWith("n1", "accepted")
    );
  });

  it("Suggest prerequisites calls inferDeps and renders the count summary", async () => {
    vi.mocked(inferDeps).mockResolvedValue({
      suggested_created: 3,
      missing_flagged: 1,
    });

    renderPanel();
    fireEvent.click(await screen.findByTestId("copilot-infer-btn"));

    await waitFor(() => expect(inferDeps).toHaveBeenCalledWith("course-1"));
    const summary = await screen.findByTestId("copilot-infer-result");
    expect(summary).toHaveTextContent("3 prerequisites suggested");
    expect(summary).toHaveTextContent("1 gap flagged");
  });

  it("shows the not-configured notice when advise 503s", async () => {
    vi.mocked(advise).mockRejectedValue({ response: { status: 503 } });

    renderPanel();
    fireEvent.click(await screen.findByTestId("copilot-advise-btn"));

    expect(
      await screen.findByTestId("copilot-ai-not-configured")
    ).toBeInTheDocument();
  });

  it("shows the not-configured notice when inferDeps 503s", async () => {
    vi.mocked(inferDeps).mockRejectedValue({ response: { status: 503 } });

    renderPanel();
    fireEvent.click(await screen.findByTestId("copilot-infer-btn"));

    expect(
      await screen.findByTestId("copilot-ai-not-configured")
    ).toBeInTheDocument();
  });

  it("clicking Dismiss calls updateAdvisorNote with dismissed", async () => {
    vi.mocked(listAdvisorNotes).mockResolvedValue([
      makeNote({ id: "n1", text: "A suggestion", status: "open" }),
    ]);

    renderPanel();
    fireEvent.click(await screen.findByTestId("advisor-note-dismiss"));

    await waitFor(() =>
      expect(updateAdvisorNote).toHaveBeenCalledWith("n1", "dismissed")
    );
  });
});
