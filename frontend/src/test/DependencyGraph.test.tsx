/**
 * Smoke tests for DependencyGraph component.
 *
 * ReactFlow relies on ResizeObserver and canvas APIs not available in jsdom,
 * so we mock the `reactflow` module entirely and verify our wrapper renders
 * the correct nodes and flags misaligned ones.
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeAll } from "vitest";
import * as React from "react";
import { MemoryRouter } from "react-router-dom";

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
// Mock reactflow so tests don't need a real DOM canvas / SVG environment.
// The factory uses React directly (imported above) — avoids require().
// ---------------------------------------------------------------------------

vi.mock("reactflow", async () => {
  type RFNode = { id: string; data: { label: React.ReactNode; _apiNode: Record<string, unknown> } };

  function ReactFlow({
    nodes,
    children,
    onNodeClick,
  }: {
    nodes: RFNode[];
    children?: React.ReactNode;
    onNodeClick?: (event: React.MouseEvent, node: RFNode) => void;
  }) {
    return React.createElement(
      "div",
      { "data-testid": "reactflow-mock" },
      nodes.map((n) =>
        React.createElement(
          "div",
          {
            key: n.id,
            "data-testid": `rf-node-${n.id}`,
            onClick: (e: React.MouseEvent) => onNodeClick?.(e, n),
          },
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

// Import after mock declaration (Vitest hoists vi.mock calls automatically)
import { DependencyGraph } from "../components/DependencyGraph";

// DependencyGraph now uses useNavigate, so it must be wrapped in a Router.
function renderGraph(props: React.ComponentProps<typeof DependencyGraph>) {
  return render(
    <MemoryRouter>
      <DependencyGraph {...props} />
    </MemoryRouter>
  );
}

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const mockNodes = [
  {
    id: "node-lo",
    kind: "learning_objectives",
    label: "lo_key",
    latest_version: "1.0.0",
    status: "active",
  },
  {
    id: "node-assess",
    kind: "assessment",
    label: "assess_key",
    latest_version: "1.1.0",
    status: "active",
  },
  {
    id: "node-rubric",
    kind: "rubric",
    label: "rubric_key",
    latest_version: "1.0.0",
    status: "draft",
  },
];

const mockEdges = [
  { from_asset_id: "node-lo", to_asset_id: "node-assess", edge_type: "depends_on" },
  { from_asset_id: "node-assess", to_asset_id: "node-rubric", edge_type: "depends_on" },
];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("DependencyGraph", () => {
  it("renders a node for each asset in the mock data", () => {
    renderGraph({
      nodes: mockNodes,
      edges: mockEdges,
      misalignedAssetIds: [],
    });

    expect(screen.getByText("lo_key")).toBeInTheDocument();
    expect(screen.getByText("assess_key")).toBeInTheDocument();
    expect(screen.getByText("rubric_key")).toBeInTheDocument();
  });

  it("shows asset kind text in node labels", () => {
    renderGraph({
      nodes: mockNodes,
      edges: mockEdges,
      misalignedAssetIds: [],
    });

    expect(screen.getByText("learning objectives")).toBeInTheDocument();
    expect(screen.getByText("assessment")).toBeInTheDocument();
    expect(screen.getByText("rubric")).toBeInTheDocument();
  });

  it("shows semver version in node labels", () => {
    renderGraph({
      nodes: mockNodes,
      edges: mockEdges,
      misalignedAssetIds: [],
    });

    expect(screen.getByText("v1.1.0")).toBeInTheDocument();
  });

  it("applies stale warning text to misaligned nodes", () => {
    renderGraph({
      nodes: mockNodes,
      edges: mockEdges,
      misalignedAssetIds: ["node-rubric"],
    });

    expect(screen.getByText("⚠ stale")).toBeInTheDocument();
  });

  it("does NOT apply stale warning when no misaligned nodes", () => {
    renderGraph({
      nodes: mockNodes,
      edges: mockEdges,
      misalignedAssetIds: [],
    });

    expect(screen.queryByText("⚠ stale")).not.toBeInTheDocument();
  });

  it("renders the reactflow container", () => {
    renderGraph({
      nodes: mockNodes,
      edges: mockEdges,
      misalignedAssetIds: [],
    });

    expect(screen.getByTestId("reactflow-mock")).toBeInTheDocument();
  });
});
