import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Player } from "../pages/learn/Player";

vi.mock("../api/learn", () => ({
  getCourse: vi.fn(),
  getCourseDecks: vi.fn(),
  setProgress: vi.fn(),
  submitAssessment: vi.fn(),
}));

import {
  getCourse,
  getCourseDecks,
  setProgress,
  submitAssessment,
} from "../api/learn";

const STRUCTURE = {
  enrollment_id: "enr-1",
  curriculum_version_id: "ver-1",
  title: "Agentic AI Architecture",
  status: "active",
  completed_items: 1,
  total_items: 3,
  items: [
    {
      member_id: "m-1",
      section: "Foundations",
      week_index: 1,
      order: 0,
      kind: "lesson_plan",
      lineage_key: "agentic-ai/v1/01/lesson_plan",
      content: "# Intro\n\nWelcome to the course.",
      media: [],
      progress_status: "complete",
    },
    {
      member_id: "m-2",
      section: "Foundations",
      week_index: 1,
      order: 1,
      kind: "lesson_plan",
      lineage_key: "agentic-ai/v1/01/lesson_plan_b",
      content: "## Details\n\nMore reading with media.\n![[media:media-1]]",
      media: [
        {
          id: "media-1",
          kind: "image",
          filename: "diagram.png",
          url: "https://cdn.example/diagram.png?sig=abc",
        },
      ],
      progress_status: "not_started",
    },
    {
      member_id: "m-3",
      section: "Assessment",
      week_index: 2,
      order: 0,
      kind: "assessment",
      lineage_key: "agentic-ai/v1/02/assessment",
      content: "Answer the following question.",
      media: [],
      progress_status: "not_started",
    },
  ],
};

function renderPlayer() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/learn/courses/enr-1"]}>
        <Routes>
          <Route path="/learn/courses/:enrollmentId" element={<Player />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Player page", () => {
  beforeEach(() => {
    vi.mocked(getCourse).mockResolvedValue(STRUCTURE);
    vi.mocked(getCourseDecks).mockResolvedValue([]);
    vi.mocked(setProgress).mockResolvedValue({
      member_id: "m-2",
      status: "complete",
      enrollment_status: "active",
      completed_items: 2,
      total_items: 3,
    });
    vi.mocked(submitAssessment).mockResolvedValue({
      id: "sub-1",
      enrollment_id: "enr-1",
      content_member_id: "m-3",
      submitted_at: "2026-07-01T00:00:00Z",
      score: null,
      feedback: null,
    });
  });

  it("renders the course title, progress, and items grouped by week", async () => {
    renderPlayer();
    await waitFor(() =>
      expect(screen.getByText("Agentic AI Architecture")).toBeInTheDocument()
    );
    expect(screen.getByText(/1 \/ 3 items complete/)).toBeInTheDocument();
    expect(screen.getByText("Week 1")).toBeInTheDocument();
    expect(screen.getByText("Week 2")).toBeInTheDocument();
    expect(screen.getAllByTestId("rail-item").length).toBe(3);
  });

  it("renders selected item content, defaulting to the first item", async () => {
    renderPlayer();
    await waitFor(() => expect(screen.getByText("Intro")).toBeInTheDocument());
    expect(screen.getByText("Welcome to the course.")).toBeInTheDocument();
  });

  it("renders inline media embeds using the presigned URL", async () => {
    const user = userEvent.setup();
    renderPlayer();
    await waitFor(() =>
      expect(screen.getAllByTestId("rail-item").length).toBe(3)
    );
    // Select the item that carries a media embed token.
    await user.click(screen.getAllByTestId("rail-item")[1]);
    await waitFor(() => {
      const img = screen.getByAltText("diagram.png") as HTMLImageElement;
      expect(img.src).toBe("https://cdn.example/diagram.png?sig=abc");
    });
  });

  it("calls /progress when Mark complete is clicked", async () => {
    const user = userEvent.setup();
    renderPlayer();
    await waitFor(() =>
      expect(screen.getAllByTestId("rail-item").length).toBe(3)
    );
    // Select the not-started item so the button is enabled.
    await user.click(screen.getAllByTestId("rail-item")[1]);
    const btn = await screen.findByTestId("mark-complete-btn");
    await user.click(btn);
    await waitFor(() =>
      expect(setProgress).toHaveBeenCalledWith("enr-1", "m-2", "complete")
    );
  });

  it("submits an assessment response via /submit", async () => {
    const user = userEvent.setup();
    renderPlayer();
    await waitFor(() =>
      expect(screen.getAllByTestId("rail-item").length).toBe(3)
    );
    await user.click(screen.getAllByTestId("rail-item")[2]);
    const panel = await screen.findByTestId("assessment-panel");
    await user.type(within(panel).getByTestId("assessment-input"), "My answer");
    await user.click(within(panel).getByTestId("submit-btn"));
    await waitFor(() =>
      expect(submitAssessment).toHaveBeenCalledWith("enr-1", "m-3", "My answer")
    );
  });
});
