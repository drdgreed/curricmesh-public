import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AiSpendTile } from "../components/AiSpendTile";

// Mock the api/client module so we control getAiUsage.
vi.mock("../api/client", () => ({
  getAiUsage: vi.fn(),
  apiClient: { interceptors: { request: { use: vi.fn() } } },
}));

import { getAiUsage } from "../api/client";

const mockUsage = {
  total_calls: 99,
  total_cost_usd: 12.34,
  persisted: {
    total_calls: 42,
    total_input_tokens: 1000,
    total_output_tokens: 500,
    total_cost_usd: 7.89,
    by_model: {
      "claude-opus": {
        calls: 42,
        input_tokens: 1000,
        output_tokens: 500,
        cost_usd: 7.89,
      },
    },
    by_day: [
      { date: "2026-05-30", calls: 10, cost_usd: 1.0 },
      { date: "2026-05-31", calls: 20, cost_usd: 3.0 },
      { date: "2026-06-01", calls: 12, cost_usd: 3.89 },
    ],
  },
};

function renderTile() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <AiSpendTile />
    </QueryClientProvider>
  );
}

describe("AiSpendTile", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the persisted total and call count", async () => {
    vi.mocked(getAiUsage).mockResolvedValue(mockUsage);
    renderTile();
    await waitFor(() => {
      expect(screen.getByTestId("ai-spend-total")).toHaveTextContent("$7.89");
    });
    // Total calls from the persisted block, plus token total (1500 → "1,500").
    expect(screen.getByText(/42 calls/)).toBeInTheDocument();
    expect(screen.getByText(/1,500 tokens/)).toBeInTheDocument();
  });

  it("renders one sparkline bar per by_day entry", async () => {
    vi.mocked(getAiUsage).mockResolvedValue(mockUsage);
    renderTile();
    await waitFor(() => {
      expect(screen.getByTestId("ai-spend-tile")).toBeInTheDocument();
    });
    expect(screen.getAllByTestId("ai-spend-bar")).toHaveLength(3);
  });

  it("renders nothing when getAiUsage rejects (e.g. 403)", async () => {
    vi.mocked(getAiUsage).mockRejectedValue(new Error("403"));
    renderTile();
    // After the query settles, the tile must not be present.
    await waitFor(() => {
      expect(getAiUsage).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.queryByTestId("ai-spend-tile")).not.toBeInTheDocument();
    });
  });
});
