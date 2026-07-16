/**
 * Smoke tests for the Graph page.
 *
 * Mocks the api/client module (no network) and reactflow (no canvas).
 * Verifies that the page fetches graph data and renders node labels.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as React from "react";

// ---------------------------------------------------------------------------
// Polyfill ResizeObserver for jsdom
// ---------------------------------------------------------------------------

beforeAll(() => {
  if (typeof ResizeObserver === "undefined") {
    // @ts-expect-error — jsdom doesn't have ResizeObserver
    global.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

// ---------------------------------------------------------------------------
// Mock reactflow (same strategy as DependencyGraph.test.tsx)
// ---------------------------------------------------------------------------

vi.mock("reactflow", async () => {
  type RFNode = { id: string; data: { label: React.ReactNode } };

  function ReactFlow({
    nodes,
    children,
  }: {
    nodes: RFNode[];
    children?: React.ReactNode;
  }) {
    return React.createElement(
      "div",
      { "data-testid": "reactflow-mock" },
      nodes.map((n) =>
        React.createElement(
          "div",
          { key: n.id, "data-testid": `rf-node-${n.id}` },
          n.data.label
        )
      ),
      children
    );
  }

  return {
    __esModule: true,
    default: ReactFlow,
    ReactFlow,
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
  };
});

// ---------------------------------------------------------------------------
// Mock api/client — no network calls
// ---------------------------------------------------------------------------

vi.mock("../api/client", () => ({
  getDashboard: vi.fn(),
  getGraph: vi.fn(),
  apiClient: { interceptors: { request: { use: vi.fn() } } },
}));

import { getDashboard, getGraph } from "../api/client";
import { Graph } from "../pages/Graph";

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const mockDashboard = {
  curricula: [
    {
      id: "curriculum-1",
      name: "Test Bootcamp",
      slug: "test-bootcamp",
      current_version_id: "version-1",
      versions: [
        { id: "version-1", semver: "1.0.0", status: "active", created_at: "2026-01-01T00:00:00Z" },
      ],
      cohorts: [],
      alignment: [],
    },
  ],
  recent_events: [],
};

const mockGraph = {
  nodes: [
    { id: "node-lo", kind: "learning_objectives", label: "intro_lo", latest_version: "1.0.0", status: "active" },
    { id: "node-assess", kind: "assessment", label: "intro_assess", latest_version: "1.0.0", status: "active" },
  ],
  edges: [
    { from_asset_id: "node-lo", to_asset_id: "node-assess", edge_type: "depends_on" },
  ],
  misaligned_asset_ids: [],
};

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderGraph() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Graph />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Graph page", () => {
  beforeEach(() => {
    vi.mocked(getDashboard).mockResolvedValue(mockDashboard);
    vi.mocked(getGraph).mockResolvedValue(mockGraph);
  });

  it("renders the page heading", async () => {
    renderGraph();
    await waitFor(() => {
      expect(screen.getByText("Dependency Graph")).toBeInTheDocument();
    });
  });

  it("renders node labels from mocked getGraph data", async () => {
    renderGraph();
    await waitFor(() => {
      expect(screen.getByText("intro_lo")).toBeInTheDocument();
      expect(screen.getByText("intro_assess")).toBeInTheDocument();
    });
  });

  it("calls getGraph with the first curriculum's id", async () => {
    renderGraph();
    await waitFor(() => {
      expect(vi.mocked(getGraph)).toHaveBeenCalledWith("curriculum-1");
    });
  });

  it("shows loading spinner initially", () => {
    vi.mocked(getDashboard).mockReturnValue(new Promise(() => {}));
    renderGraph();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("shows an error alert when dashboard fails", async () => {
    vi.mocked(getDashboard).mockRejectedValue(new Error("Network error"));
    renderGraph();
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });

  it("shows empty state when graph has no nodes", async () => {
    vi.mocked(getGraph).mockResolvedValue({
      nodes: [],
      edges: [],
      misaligned_asset_ids: [],
    });
    renderGraph();
    await waitFor(() => {
      expect(
        screen.getByText(/no active version or no assets/i)
      ).toBeInTheDocument();
    });
  });
});
