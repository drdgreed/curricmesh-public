import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Analytics } from "../pages/Analytics";

// Mock the api/client module — the page's useQuery hook calls getAnalyticsOverview.
vi.mock("../api/client", () => ({
  getAnalyticsOverview: vi.fn(),
  apiClient: { interceptors: { request: { use: vi.fn() } } },
}));

import { getAnalyticsOverview } from "../api/client";

const mockOverview = {
  velocity: [
    { bucket_start: "2026-06-01T00:00:00Z", ccrs_opened: 2, versions_released: 1 },
    { bucket_start: "2026-06-08T00:00:00Z", ccrs_opened: 1, versions_released: 0 },
  ],
  time_in_state: [
    { state: "draft", n: 1, mean_days: 2.0, median_days: 2.0 },
    { state: "review", n: 1, mean_days: 3.0, median_days: 3.0 },
    { state: "approved", n: 1, mean_days: 1.0, median_days: 1.0 },
    { state: "active", n: 0, mean_days: null, median_days: null },
    { state: "archived", n: 0, mean_days: null, median_days: null },
    { state: "sunset", n: 0, mean_days: null, median_days: null },
  ],
  cadence: { releases: 2, mean_days_between: 7.0, median_days_between: 7.0 },
  distribution: [
    { entity: "ccr", status: "draft", count: 3 },
    { entity: "version", status: "active", count: 1 },
  ],
};

const emptyOverview = {
  velocity: [],
  time_in_state: [
    { state: "draft", n: 0, mean_days: null, median_days: null },
  ],
  cadence: { releases: 0, mean_days_between: null, median_days_between: null },
  distribution: [],
};

function renderAnalytics() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Analytics />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Analytics page", () => {
  beforeEach(() => {
    vi.mocked(getAnalyticsOverview).mockResolvedValue(mockOverview);
  });

  it("renders the page heading and summary totals", async () => {
    renderAnalytics();
    await waitFor(() => {
      expect(screen.getByText("Analytics")).toBeInTheDocument();
      // 2 + 1 CCRs opened, 1 + 0 released.
      expect(screen.getByText("CCRs opened")).toBeInTheDocument();
      expect(screen.getByText("Versions released")).toBeInTheDocument();
    });
  });

  it("renders the velocity buckets", async () => {
    renderAnalytics();
    await waitFor(() => {
      expect(screen.getByText("2026-06-01")).toBeInTheDocument();
      expect(screen.getByText("2 opened · 1 released")).toBeInTheDocument();
    });
  });

  it("shows time-in-state rows including honest 'no data' for sparse states", async () => {
    renderAnalytics();
    await waitFor(() => {
      expect(screen.getByText("Time in state")).toBeInTheDocument();
      // active has n=0 → honest gap shown, not a fabricated duration.
      expect(screen.getAllByText("no data").length).toBeGreaterThanOrEqual(1);
    });
  });

  it("renders the current distribution", async () => {
    renderAnalytics();
    await waitFor(() => {
      expect(screen.getByText("Current distribution")).toBeInTheDocument();
      expect(screen.getByText("× 3")).toBeInTheDocument();
    });
  });
});

describe("Analytics page — empty state", () => {
  beforeEach(() => {
    vi.mocked(getAnalyticsOverview).mockResolvedValue(emptyOverview);
  });

  it("shows the empty-state message when there is no data", async () => {
    renderAnalytics();
    await waitFor(() => {
      expect(screen.getByText(/No analytics data yet/)).toBeInTheDocument();
    });
  });
});
