import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Login } from "../pages/Login";
import { AuthProvider } from "../auth/AuthContext";

// Render Login inside minimal providers (no actual network calls)
function renderLogin() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Login />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe("Login page", () => {
  it("renders the email and password fields", () => {
    renderLogin();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it("renders the sign in button", () => {
    renderLogin();
    expect(
      screen.getByRole("button", { name: /sign in/i })
    ).toBeInTheDocument();
  });

  it("renders the CurricMesh heading", () => {
    renderLogin();
    expect(screen.getAllByText(/curricmesh/i).length).toBeGreaterThan(0);
  });

  it("does not show an error alert by default", () => {
    renderLogin();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

// Suppress unused var warning from vi in case it's imported but not used
vi.mock("../api/client", () => ({
  login: vi.fn().mockResolvedValue({ access_token: "tok", token_type: "bearer" }),
  apiClient: { interceptors: { request: { use: vi.fn() } } },
}));
