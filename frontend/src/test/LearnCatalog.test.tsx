import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Catalog } from "../pages/learn/Catalog";

// Mock the learner API layer — the page's hooks call these.
vi.mock("../api/learn", () => ({
  getCatalog: vi.fn(),
  getEnrollments: vi.fn(),
  enroll: vi.fn(),
}));

import { getCatalog, getEnrollments, enroll } from "../api/learn";

const CATALOG = [
  {
    curriculum_version_id: "ver-1",
    curriculum_id: "cur-1",
    title: "Agentic AI Architecture",
    version: "1.1.0",
  },
  {
    curriculum_version_id: "ver-2",
    curriculum_id: "cur-2",
    title: "Cloud Data Engineering",
    version: "1.0.0",
  },
];

function renderCatalog() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Catalog />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Catalog page", () => {
  beforeEach(() => {
    vi.mocked(getCatalog).mockResolvedValue(CATALOG);
    vi.mocked(getEnrollments).mockResolvedValue([]);
    vi.mocked(enroll).mockResolvedValue({
      id: "enr-1",
      curriculum_version_id: "ver-1",
      learner_id: "l-1",
      status: "active",
      title: "Agentic AI Architecture",
      completed_items: 0,
      total_items: 10,
      enrolled_at: "2026-07-01T00:00:00Z",
      completed_at: null,
    });
  });

  it("renders released courses from the catalog", async () => {
    renderCatalog();
    await waitFor(() => {
      expect(screen.getByText("Agentic AI Architecture")).toBeInTheDocument();
      expect(screen.getByText("Cloud Data Engineering")).toBeInTheDocument();
      expect(screen.getByText("v1.1.0")).toBeInTheDocument();
    });
  });

  it("calls the enroll API when Enroll is clicked", async () => {
    const user = userEvent.setup();
    renderCatalog();
    await waitFor(() =>
      expect(screen.getByText("Agentic AI Architecture")).toBeInTheDocument()
    );
    const buttons = screen.getAllByTestId("enroll-btn");
    await user.click(buttons[0]);
    await waitFor(() => expect(enroll).toHaveBeenCalledWith("ver-1"));
  });

  it("shows an Enrolled chip for already-enrolled courses", async () => {
    vi.mocked(getEnrollments).mockResolvedValue([
      {
        id: "enr-9",
        curriculum_version_id: "ver-1",
        learner_id: "l-1",
        status: "active",
        title: "Agentic AI Architecture",
        completed_items: 2,
        total_items: 10,
        enrolled_at: "2026-07-01T00:00:00Z",
        completed_at: null,
      },
    ]);
    renderCatalog();
    await waitFor(() => expect(screen.getByText("Enrolled")).toBeInTheDocument());
  });
});
