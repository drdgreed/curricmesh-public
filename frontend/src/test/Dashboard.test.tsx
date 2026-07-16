import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Dashboard } from "../pages/Dashboard";

// Mock the entire api/client module
vi.mock("../api/client", () => ({
  getDashboard: vi.fn(),
  getAlignment: vi.fn(),
  // The Dashboard now renders <AiSpendTile/> for author roles, which calls
  // getAiUsage. Stub it; tests that don't care leave it rejecting so the tile
  // fails silently (renders null) and doesn't interfere with assertions.
  getAiUsage: vi.fn(),
  apiClient: { interceptors: { request: { use: vi.fn() } } },
}));

// Dashboard now reads the current role via useAuth to gate the
// "New Change Request" button. Mock it so the page renders without
// an AuthProvider wrapper. mockRole is controllable so we can exercise
// the role-gating of the "New Change Request" button.
let mockRole = "architect";
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ role: mockRole, token: "t", org: "o", login: vi.fn(), logout: vi.fn() }),
}));

import { getAlignment, getDashboard } from "../api/client";

// Precise per-curriculum alignment, keyed by curriculum id. The Dashboard now
// sources its out-of-alignment rows from getAlignment (mode + revision_delta),
// not the legacy curriculum.alignment timestamps.
const alignmentByCurriculum: Record<
  string,
  {
    items: {
      dependent_id: string;
      dependent_label: string;
      prerequisite_id: string;
      prerequisite_label: string;
      mode: "revision" | "timestamp";
      revision_delta: number | null;
    }[];
  }
> = {};

function mockAlignment() {
  vi.mocked(getAlignment).mockImplementation(async (id: string) => ({
    items: alignmentByCurriculum[id]?.items ?? [],
  }));
}

const mockDashboard = {
  curricula: [
    {
      id: "c1",
      name: "Data Engineering Bootcamp",
      slug: "data-eng-bootcamp",
      current_version_id: "v2",
      versions: [
        { id: "v1", semver: "1.0.0", status: "archived", created_at: "2026-01-01T00:00:00Z" },
        { id: "v2", semver: "2.0.0", status: "active", created_at: "2026-02-01T00:00:00Z" },
      ],
      cohorts: [
        {
          id: "co1",
          name: "Cohort Spring 2026",
          version_id: "v2",
          start_date: "2026-01-01T00:00:00",
          end_date: "2099-12-31T00:00:00",
        },
      ],
      alignment: [],
    },
    {
      id: "c2",
      name: "ML Fundamentals",
      slug: "ml-fundamentals",
      current_version_id: null,
      versions: [
        { id: "v3", semver: "1.0.0", status: "draft", created_at: "2026-03-01T00:00:00Z" },
      ],
      cohorts: [],
      alignment: [],
    },
  ],
  recent_events: [],
};

const mockDashboardWithAlignment = {
  curricula: [
    {
      id: "c1",
      name: "Data Engineering Bootcamp",
      slug: "data-eng-bootcamp",
      current_version_id: "v2",
      versions: [
        { id: "v2", semver: "2.0.0", status: "active", created_at: "2026-02-01T00:00:00Z" },
      ],
      cohorts: [],
      alignment: [
        {
          dependent_asset_id: "asset-a",
          dependency_asset_id: "asset-b",
          dependent_asset_name: "Week 3: Joins · Coding Lab",
          dependency_asset_name: "Week 3: Joins · Spec",
          dependent_updated_at: "2026-02-01T00:00:00Z",
          dependency_updated_at: "2026-03-01T00:00:00Z",
          reason: "dependency is stale",
        },
        {
          dependent_asset_id: "asset-c",
          dependency_asset_id: "asset-d",
          dependent_asset_name: "Week 4: Windows · Slides",
          dependency_asset_name: "Week 4: Windows · Lesson Plan",
          dependent_updated_at: "2026-02-10T00:00:00Z",
          dependency_updated_at: "2026-03-10T00:00:00Z",
          reason: "version mismatch",
        },
      ],
    },
    {
      id: "c2",
      name: "ML Fundamentals",
      slug: "ml-fundamentals",
      current_version_id: null,
      versions: [],
      cohorts: [],
      alignment: [],
    },
  ],
  recent_events: [],
};

function renderDashboard() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Dashboard page", () => {
  beforeEach(() => {
    mockRole = "architect";
    vi.mocked(getDashboard).mockResolvedValue(mockDashboard);
    Object.keys(alignmentByCurriculum).forEach(
      (k) => delete alignmentByCurriculum[k]
    );
    mockAlignment();
  });

  it("renders curriculum names from mock data", async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText("Data Engineering Bootcamp")).toBeInTheDocument();
      expect(screen.getByText("ML Fundamentals")).toBeInTheDocument();
    });
  });

  it("renders status badges for versions", async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText("Active")).toBeInTheDocument();
      expect(screen.getByText("Archived")).toBeInTheDocument();
      expect(screen.getByText("Draft")).toBeInTheDocument();
    });
  });

  it("marks the current version", async () => {
    renderDashboard();
    await waitFor(() => {
      // v2 is current_version_id for c1 — "current" chip should appear
      expect(screen.getByText("current")).toBeInTheDocument();
    });
  });

  it("shows curriculum slugs", async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText("data-eng-bootcamp")).toBeInTheDocument();
    });
  });

  it("does not show alignment warning when alignment array is empty", async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.queryByText(/out-of-alignment/)).not.toBeInTheDocument();
    });
  });
});

describe("Dashboard page — New Change Request role gating", () => {
  beforeEach(() => {
    vi.mocked(getDashboard).mockResolvedValue(mockDashboard);
    Object.keys(alignmentByCurriculum).forEach(
      (k) => delete alignmentByCurriculum[k]
    );
    mockAlignment();
  });

  it("shows the New Change Request action for an author role (architect)", async () => {
    mockRole = "architect";
    renderDashboard();
    // The action is a MUI Button rendered as a RouterLink (role=link).
    await waitFor(() => {
      expect(
        screen.getByRole("link", { name: /new change request/i })
      ).toBeInTheDocument();
    });
  });

  it("hides the New Change Request action for a non-author role (qa_lead)", async () => {
    mockRole = "qa_lead";
    renderDashboard();
    // Wait for data to load so the gated action would have rendered if allowed.
    await waitFor(() => {
      expect(screen.getByText("Data Engineering Bootcamp")).toBeInTheDocument();
    });
    // Per the role-gate: neither a button nor a link variant should be present.
    expect(
      screen.queryByRole("button", { name: /new change request/i })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /new change request/i })
    ).not.toBeInTheDocument();
  });
});

describe("Dashboard page — alignment warnings", () => {
  beforeEach(() => {
    mockRole = "architect";
    vi.mocked(getDashboard).mockResolvedValue(mockDashboardWithAlignment);
    Object.keys(alignmentByCurriculum).forEach(
      (k) => delete alignmentByCurriculum[k]
    );
    // c1 has two out-of-alignment dependents; c2 has none.
    alignmentByCurriculum["c1"] = {
      items: [
        {
          dependent_id: "asset-a",
          dependent_label: "Week 3: Joins · Coding Lab",
          prerequisite_id: "asset-b",
          prerequisite_label: "Week 3: Joins · Spec",
          mode: "timestamp",
          revision_delta: null,
        },
        {
          dependent_id: "asset-c",
          dependent_label: "Week 4: Windows · Slides",
          prerequisite_id: "asset-d",
          prerequisite_label: "Week 4: Windows · Lesson Plan",
          mode: "revision",
          revision_delta: 2,
        },
      ],
    };
    mockAlignment();
  });

  it("shows alignment warning with count for curriculum with misaligned assets", async () => {
    renderDashboard();
    await waitFor(() => {
      expect(screen.getByText(/2 out-of-alignment assets/)).toBeInTheDocument();
    });
  });

  it("does not show alignment warning for curriculum with empty alignment", async () => {
    renderDashboard();
    await waitFor(() => {
      // Only one warning should appear (for c1), not two
      const warnings = screen.getAllByText(/out-of-alignment/);
      expect(warnings).toHaveLength(1);
    });
  });

  it("renders a collapsible Accordion showing friendly names (no UUIDs)", async () => {
    renderDashboard();

    // The summary renders as a button and is collapsed by default.
    await waitFor(() => {
      expect(screen.getByText(/2 out-of-alignment assets/)).toBeInTheDocument();
    });
    const summaryButton = screen.getByRole("button", {
      name: /out-of-alignment assets/i,
    });
    expect(summaryButton).toHaveAttribute("aria-expanded", "false");

    // Expand the accordion, then assert the friendly names are revealed in the
    // region (and no raw UUIDs anywhere).
    fireEvent.click(summaryButton);
    await waitFor(() => {
      expect(summaryButton).toHaveAttribute("aria-expanded", "true");
    });
    const region = screen.getByRole("region");
    expect(within(region).getByText(/Week 3: Joins · Coding Lab/)).toBeInTheDocument();
    expect(within(region).getByText(/Week 3: Joins · Spec/)).toBeInTheDocument();
    expect(within(region).getByText(/Week 4: Windows · Slides/)).toBeInTheDocument();
    // Mode-aware staleness chips: timestamp → "needs review", revision → "N revisions behind".
    expect(within(region).getByText(/needs review/i)).toBeInTheDocument();
    expect(within(region).getByText(/2 revisions behind/i)).toBeInTheDocument();
    expect(screen.queryByText(/asset-a/)).not.toBeInTheDocument();
    expect(screen.queryByText(/asset-b/)).not.toBeInTheDocument();
  });
});
