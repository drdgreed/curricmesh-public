import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { History } from "../pages/History";

// Mock the api/client module (History reads the dashboard endpoint).
vi.mock("../api/client", () => ({
  getDashboard: vi.fn(),
  apiClient: { interceptors: { request: { use: vi.fn() } } },
}));

import { getDashboard } from "../api/client";

const mockDashboard = {
  curricula: [],
  recent_events: [
    {
      id: "e1",
      event_type: "version_active",
      target: "11111111-1111-1111-1111-111111111111",
      actor_id: "22222222-2222-2222-2222-222222222222",
      actor_label: "Ada Lovelace",
      target_label: "v2.0.0",
      details: {},
      created_at: "2026-05-01T12:00:00Z",
    },
    {
      id: "e2",
      event_type: "ccr_created",
      target: "ccr:33333333-3333-3333-3333-333333333333",
      actor_id: "44444444-4444-4444-4444-444444444444",
      actor_label: null,
      target_label: "Refresh ML module",
      details: {},
      created_at: "2026-05-02T12:00:00Z",
    },
  ],
};

function renderHistory() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <History />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("History page", () => {
  beforeEach(() => {
    vi.mocked(getDashboard).mockResolvedValue(mockDashboard as never);
  });

  it("renders actor labels and humanized event types", async () => {
    renderHistory();
    await waitFor(() => {
      expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    });
    // Humanized event-type chips.
    expect(screen.getByText("Version activated")).toBeInTheDocument();
    expect(screen.getByText("Change request opened")).toBeInTheDocument();
  });

  it("renders friendly target labels and falls back to 'System' for a missing actor", async () => {
    renderHistory();
    await waitFor(() => {
      expect(screen.getByText(/v2\.0\.0/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Refresh ML module/)).toBeInTheDocument();
    // actor_label was null for the ccr_created event → "System".
    expect(screen.getByText("System")).toBeInTheDocument();
  });

  it("never renders raw target/actor UUIDs", async () => {
    renderHistory();
    await waitFor(() => {
      expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    });
    expect(
      screen.queryByText(/11111111-1111-1111-1111-111111111111/)
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/22222222-2222-2222-2222-222222222222/)
    ).not.toBeInTheDocument();
  });
});
