import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { AuthProvider, useAuth } from "../auth/AuthContext";

// Mock the api/client module — only login/getMe are used by AuthContext.
vi.mock("../api/client", () => ({
  login: vi.fn(),
  getMe: vi.fn(),
  apiClient: { interceptors: { request: { use: vi.fn() } } },
}));

import { getMe } from "../api/client";

const TOKEN_KEY = "auth_token";
const ORG_NAME_KEY = "auth_org_name";

// Small probe component that surfaces auth state for assertions.
function Probe() {
  const { orgName, role } = useAuth();
  return (
    <div>
      <span data-testid="org-name">{orgName ?? "—"}</span>
      <span data-testid="role">{role ?? "—"}</span>
    </div>
  );
}

function renderWithAuth() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>
  );
}

describe("AuthContext self-heal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("calls getMe on mount when a token exists but orgName is missing", async () => {
    localStorage.setItem(TOKEN_KEY, "stale-token");
    // No org_name stored — simulates a pre-fix session.
    vi.mocked(getMe).mockResolvedValue({
      sub: "u1",
      role: "architect",
      org: "org-uuid",
      org_name: "Career Forge",
    });

    renderWithAuth();

    await waitFor(() => {
      expect(getMe).toHaveBeenCalledTimes(1);
    });
    // State (and localStorage) is backfilled from /auth/me.
    await waitFor(() => {
      expect(screen.getByTestId("org-name")).toHaveTextContent("Career Forge");
    });
    expect(screen.getByTestId("role")).toHaveTextContent("architect");
    expect(localStorage.getItem(ORG_NAME_KEY)).toBe("Career Forge");
  });

  it("does not call getMe when orgName is already present", async () => {
    localStorage.setItem(TOKEN_KEY, "good-token");
    localStorage.setItem(ORG_NAME_KEY, "Existing Org");

    renderWithAuth();

    // Give any (unwanted) effect a chance to fire.
    await waitFor(() => {
      expect(screen.getByTestId("org-name")).toHaveTextContent("Existing Org");
    });
    expect(getMe).not.toHaveBeenCalled();
  });

  it("does not call getMe when there is no token", async () => {
    renderWithAuth();
    await waitFor(() => {
      expect(screen.getByTestId("org-name")).toHaveTextContent("—");
    });
    expect(getMe).not.toHaveBeenCalled();
  });
});
