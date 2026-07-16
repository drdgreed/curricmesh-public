import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MyCourses } from "../pages/learn/MyCourses";

vi.mock("../api/learn", () => ({
  getEnrollments: vi.fn(),
}));

import { getEnrollments } from "../api/learn";

function renderMyCourses() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <MyCourses />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("MyCourses page", () => {
  beforeEach(() => {
    vi.mocked(getEnrollments).mockResolvedValue([
      {
        id: "enr-1",
        curriculum_version_id: "ver-1",
        learner_id: "l-1",
        status: "active",
        title: "Agentic AI Architecture",
        completed_items: 3,
        total_items: 12,
        enrolled_at: "2026-07-01T00:00:00Z",
        completed_at: null,
      },
    ]);
  });

  it("renders each enrollment with its progress summary", async () => {
    renderMyCourses();
    await waitFor(() =>
      expect(screen.getByText("Agentic AI Architecture")).toBeInTheDocument()
    );
    expect(screen.getByText(/3 \/ 12 items/)).toBeInTheDocument();
    expect(screen.getByText(/25%/)).toBeInTheDocument();
  });

  it("shows an empty state when there are no enrollments", async () => {
    vi.mocked(getEnrollments).mockResolvedValue([]);
    renderMyCourses();
    await waitFor(() =>
      expect(
        screen.getByText(/not enrolled in any courses yet/i)
      ).toBeInTheDocument()
    );
  });
});
