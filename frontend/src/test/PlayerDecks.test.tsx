import { render, screen, waitFor } from "@testing-library/react";
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

import { getCourse, getCourseDecks } from "../api/learn";

const STRUCTURE = {
  enrollment_id: "enr-1",
  curriculum_version_id: "ver-1",
  title: "Agentic AI Architecture",
  status: "active",
  completed_items: 0,
  total_items: 1,
  items: [
    {
      member_id: "m-1",
      section: "Foundations",
      week_index: 1,
      order: 0,
      kind: "lesson_plan",
      lineage_key: "agentic-ai/v1/01/lesson_plan",
      content: "# Intro",
      media: [],
      progress_status: "not_started",
    },
  ],
};

const DECK = {
  id: "deck-1",
  source_member_id: null,
  status: "ready",
  created_at: "2026-07-07T00:00:00Z",
  html_url: "https://cdn.example/decks/deck-1/deck.html?sig=h",
  pdf_url: "https://cdn.example/decks/deck-1/deck.pdf?sig=p",
  pptx_url: "https://cdn.example/decks/deck-1/deck.pptx?sig=x",
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

describe("Player slide-deck surface", () => {
  beforeEach(() => {
    vi.mocked(getCourse).mockResolvedValue(STRUCTURE);
  });

  it("embeds the HTML slides and shows PDF/PPTX download buttons", async () => {
    vi.mocked(getCourseDecks).mockResolvedValue([DECK]);
    renderPlayer();

    await waitFor(() =>
      expect(screen.getByTestId("deck-panel")).toBeInTheDocument()
    );
    // View: the HTML slides are embedded via the presigned URL.
    const frame = screen.getByTestId("deck-frame") as HTMLIFrameElement;
    expect(frame.getAttribute("src")).toBe(DECK.html_url);
    // Download: PDF + PPTX buttons link to the presigned artifact URLs.
    const pdf = screen.getByTestId("deck-download-pdf") as HTMLAnchorElement;
    const pptx = screen.getByTestId("deck-download-pptx") as HTMLAnchorElement;
    expect(pdf.getAttribute("href")).toBe(DECK.pdf_url);
    expect(pptx.getAttribute("href")).toBe(DECK.pptx_url);
    // Reachability: the deck surface is mounted inside the player (no navigation).
    expect(getCourseDecks).toHaveBeenCalledWith("enr-1");
  });

  it("renders no deck surface when the course has no decks", async () => {
    vi.mocked(getCourseDecks).mockResolvedValue([]);
    renderPlayer();

    // Wait for the player to settle (title rendered).
    await waitFor(() =>
      expect(screen.getByText("Agentic AI Architecture")).toBeInTheDocument()
    );
    await waitFor(() => expect(getCourseDecks).toHaveBeenCalled());
    // Graceful absence: no deck panel is shown.
    expect(screen.queryByTestId("deck-panel")).not.toBeInTheDocument();
  });
});
