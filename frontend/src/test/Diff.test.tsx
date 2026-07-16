/**
 * Smoke tests for the Diff page (Task B5).
 *
 * Strategy: mock the api/client module; no network requests.
 */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mock the entire api/client module (no network)
// ---------------------------------------------------------------------------

vi.mock("../api/client", () => ({
  apiClient: { interceptors: { request: { use: vi.fn() } } },
  listAssetVersions: vi.fn(),
  getDiff: vi.fn(),
}));

import { listAssetVersions, getDiff } from "../api/client";

// ---------------------------------------------------------------------------
// After mock declaration, import the component under test
// ---------------------------------------------------------------------------

import { Diff } from "../pages/Diff";

// ---------------------------------------------------------------------------
// Shared test data
// ---------------------------------------------------------------------------

const ASSET_ID = "asset-abc-123";

const mockVersions = [
  { id: "ver-2", semver: "1.1.0", status: "draft", created_at: "2026-02-01T00:00:00Z" },
  { id: "ver-1", semver: "1.0.0", status: "active", created_at: "2026-01-01T00:00:00Z" },
];

const mockTextDiff = {
  kind: "lesson_plan",
  text: {
    added: ["+ New line added here"],
    removed: ["- Old line removed here"],
    unified:
      "--- a\n+++ b\n@@ -1,1 +1,1 @@\n- Old line removed here\n+ New line added here",
  },
  structured: null,
};

const mockStructuredDiff = {
  kind: "rubric",
  text: null,
  structured: {
    added: [],
    removed: [],
    changed: [
      { key: "clarity", from: 0.2, to: 0.3 },
      { key: "depth", from: 0.8, to: 0.7 },
    ],
  },
};

// ---------------------------------------------------------------------------
// Render helper — sets route params and query params
// ---------------------------------------------------------------------------

function renderDiff(
  assetId: string = ASSET_ID,
  searchParams: string = "?from=ver-1&to=ver-2"
) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/assets/${assetId}/diff${searchParams}`]}>
        <Routes>
          <Route path="/assets/:assetId/diff" element={<Diff />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Diff page — text diff", () => {
  beforeEach(() => {
    vi.mocked(listAssetVersions).mockResolvedValue(mockVersions);
    vi.mocked(getDiff).mockResolvedValue(mockTextDiff);
  });

  it("renders added lines in green and removed lines in red (unified mode)", async () => {
    renderDiff();

    await waitFor(() => {
      // The unified diff container should be present
      expect(screen.getByTestId("unified-diff")).toBeInTheDocument();
    });

    // Added lines are data-testid="diff-added-line"
    const addedLines = screen.getAllByTestId("diff-added-line");
    expect(addedLines.length).toBeGreaterThan(0);
    // At least one added line contains the expected text
    const addedTexts = addedLines.map((el) => el.textContent ?? "");
    expect(addedTexts.some((t) => t.includes("New line added here"))).toBe(true);

    // Removed lines are data-testid="diff-removed-line"
    const removedLines = screen.getAllByTestId("diff-removed-line");
    expect(removedLines.length).toBeGreaterThan(0);
    const removedTexts = removedLines.map((el) => el.textContent ?? "");
    expect(removedTexts.some((t) => t.includes("Old line removed here"))).toBe(true);
  });

  it("toggles from unified to side-by-side and back", async () => {
    renderDiff();

    // Wait for diff to load
    await waitFor(() => {
      expect(screen.getByTestId("unified-diff")).toBeInTheDocument();
    });

    // Click "Side-by-side"
    const sideBySideBtn = screen.getByTestId("toggle-sidebyside");
    fireEvent.click(sideBySideBtn);

    await waitFor(() => {
      expect(screen.getByTestId("sidebyside-diff")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("unified-diff")).not.toBeInTheDocument();

    // Click "Unified" to toggle back
    const unifiedBtn = screen.getByTestId("toggle-unified");
    fireEvent.click(unifiedBtn);

    await waitFor(() => {
      expect(screen.getByTestId("unified-diff")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("sidebyside-diff")).not.toBeInTheDocument();
  });

  it("side-by-side mode shows added and removed columns", async () => {
    renderDiff();

    await waitFor(() => {
      expect(screen.getByTestId("unified-diff")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("toggle-sidebyside"));

    await waitFor(() => {
      expect(screen.getByTestId("sidebyside-diff")).toBeInTheDocument();
    });

    // Column headers
    expect(screen.getByText("Removed")).toBeInTheDocument();
    expect(screen.getByText("Added")).toBeInTheDocument();

    // Content in the columns
    const addedLines = screen.getAllByTestId("diff-added-line");
    const removedLines = screen.getAllByTestId("diff-removed-line");
    expect(addedLines.length).toBeGreaterThan(0);
    expect(removedLines.length).toBeGreaterThan(0);
  });
});

describe("Diff page — structured diff (rubric)", () => {
  beforeEach(() => {
    vi.mocked(listAssetVersions).mockResolvedValue(mockVersions);
    vi.mocked(getDiff).mockResolvedValue(mockStructuredDiff);
  });

  it("renders the structured Changed table with key, from, to values", async () => {
    renderDiff();

    await waitFor(() => {
      expect(screen.getByText("Changed")).toBeInTheDocument();
    });

    // Table headers
    expect(screen.getByText("Key")).toBeInTheDocument();
    expect(screen.getByText("From")).toBeInTheDocument();
    expect(screen.getByText("To")).toBeInTheDocument();

    // clarity row: 0.2 → 0.3
    const clarityRow = screen.getByTestId("changed-row-clarity");
    expect(clarityRow).toBeInTheDocument();
    expect(clarityRow.textContent).toContain("clarity");
    expect(clarityRow.textContent).toContain("0.2");
    expect(clarityRow.textContent).toContain("0.3");

    // depth row: 0.8 → 0.7
    const depthRow = screen.getByTestId("changed-row-depth");
    expect(depthRow).toBeInTheDocument();
    expect(depthRow.textContent).toContain("0.8");
    expect(depthRow.textContent).toContain("0.7");
  });

  it("shows the asset kind chip", async () => {
    renderDiff();

    await waitFor(() => {
      expect(screen.getByText(/kind: rubric/i)).toBeInTheDocument();
    });
  });
});

describe("Diff page — version pickers", () => {
  beforeEach(() => {
    vi.mocked(listAssetVersions).mockResolvedValue(mockVersions);
    vi.mocked(getDiff).mockResolvedValue(mockTextDiff);
  });

  it("renders version picker labels from mocked listAssetVersions", async () => {
    renderDiff(ASSET_ID, "");

    await waitFor(() => {
      // MUI InputLabel renders the text in both a <label> and a <span>,
      // so use getAllByText and assert at least one instance exists.
      expect(screen.getAllByText("From (before)").length).toBeGreaterThan(0);
      expect(screen.getAllByText("To (after)").length).toBeGreaterThan(0);
    });
  });
});

describe("Diff page — 0/1 version feedback", () => {
  it("shows informational alert when asset has no versions", async () => {
    vi.mocked(listAssetVersions).mockResolvedValue([]);
    vi.mocked(getDiff).mockReturnValue(new Promise(() => {}));

    renderDiff(ASSET_ID, "");

    await waitFor(() => {
      expect(
        screen.getByText(/this asset has no versions to compare/i)
      ).toBeInTheDocument();
    });
  });

  it("shows informational alert when asset has exactly one version", async () => {
    vi.mocked(listAssetVersions).mockResolvedValue([mockVersions[0]]);
    vi.mocked(getDiff).mockReturnValue(new Promise(() => {}));

    renderDiff(ASSET_ID, "");

    await waitFor(() => {
      expect(
        screen.getByText(/only one version exists/i)
      ).toBeInTheDocument();
    });
  });
});

describe("Diff page — loading and error states", () => {
  it("shows a loading indicator while versions are fetching", async () => {
    // Never-resolving promise to keep loading state
    vi.mocked(listAssetVersions).mockReturnValue(new Promise(() => {}));
    vi.mocked(getDiff).mockReturnValue(new Promise(() => {}));

    renderDiff(ASSET_ID, "");

    expect(screen.getByText(/loading versions/i)).toBeInTheDocument();
  });

  it("shows no changes alert when diff is empty", async () => {
    vi.mocked(listAssetVersions).mockResolvedValue(mockVersions);
    vi.mocked(getDiff).mockResolvedValue({
      kind: "rubric",
      text: { added: [], removed: [], unified: "" },
      structured: { added: [], removed: [], changed: [] },
    });

    renderDiff();

    await waitFor(() => {
      expect(screen.getByText(/no changes between the selected versions/i)).toBeInTheDocument();
    });
  });
});
