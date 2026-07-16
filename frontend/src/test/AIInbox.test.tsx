import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AIInbox } from "../pages/AIInbox";

// Mock the api/client module
vi.mock("../api/client", () => ({
  getAIInbox: vi.fn(),
  submitQAReview: vi.fn(),
  apiClient: { interceptors: { request: { use: vi.fn() } } },
}));

import { getAIInbox, submitQAReview } from "../api/client";

// Controllable role for useAuth — flipped per describe block.
let mockRole = "qa_lead";
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ role: mockRole, token: "t", login: vi.fn(), logout: vi.fn() }),
}));

const mockInbox = {
  drafted_ccrs: [
    {
      id: "ccr1",
      curriculum_id: "cur1",
      title: "Add vector DB module",
      rationale: "SOTA moved to embeddings",
      proposed_bump: "minor",
      status: "draft",
      impact: {
        ai_research: {
          topic: "Vector databases",
          coverage_status: "missing",
          citations: ["https://arxiv.org/abs/1234.5678"],
        },
      },
      external_link: null,
      author_id: "ai-user",
      created_at: "2026-05-01T00:00:00Z",
    },
  ],
  draft_qa_reviews: [
    {
      id: "qa1",
      ccr_id: "ccr1",
      ccr_title: "Add vector DB module",
      dimension_scores: { clarity: 4, rigor: 3 },
      evidence: { clarity: "Objectives are explicit.", rigor: "Lacks assessment." },
      created_at: "2026-05-02T00:00:00Z",
    },
  ],
};

function renderInbox() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AIInbox />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("AIInbox page — qa_lead", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRole = "qa_lead";
    vi.mocked(getAIInbox).mockResolvedValue(mockInbox);
    vi.mocked(submitQAReview).mockResolvedValue({});
  });

  it("renders AI CCR title and a citation", async () => {
    renderInbox();
    await waitFor(() => {
      expect(screen.getAllByText("Add vector DB module").length).toBeGreaterThan(0);
      expect(screen.getByText("https://arxiv.org/abs/1234.5678")).toBeInTheDocument();
    });
  });

  it("renders QA ccr_title, dimension chips, and evidence", async () => {
    renderInbox();
    await waitFor(() => {
      expect(screen.getByText("clarity: 4")).toBeInTheDocument();
      expect(screen.getByText("rigor: 3")).toBeInTheDocument();
      expect(screen.getByText(/Lacks assessment\./)).toBeInTheDocument();
    });
  });

  it("shows Accept scores button and calls submitQAReview on click", async () => {
    renderInbox();
    const btn = await screen.findByRole("button", { name: /accept scores/i });
    fireEvent.click(btn);
    await waitFor(() => {
      expect(submitQAReview).toHaveBeenCalledWith("ccr1", { clarity: 4, rigor: 3 }, "pass");
    });
    await waitFor(() => {
      expect(getAIInbox).toHaveBeenCalledTimes(2); // initial load + post-accept refetch
    });
  });
});

describe("AIInbox page — instructor (no promote)", () => {
  beforeEach(() => {
    mockRole = "instructor";
    vi.mocked(getAIInbox).mockResolvedValue(mockInbox);
  });

  it("does not show Accept scores button for instructor role", async () => {
    renderInbox();
    await waitFor(() => {
      expect(screen.getByText("clarity: 4")).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /accept scores/i })).not.toBeInTheDocument();
  });
});
