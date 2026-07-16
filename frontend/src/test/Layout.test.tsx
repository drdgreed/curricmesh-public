/**
 * Layout nav role-gating tests.
 *
 * Verifies that the sidebar shows the right items for each role:
 *   - learner  → Dashboard + Learn only (no author tools)
 *   - architect → Dashboard + Learn + all author tools
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { Layout } from "../components/Layout";

// ── Auth ─────────────────────────────────────────────────────────────────────
let mockRole: string | null = "learner";
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    role: mockRole,
    orgName: "Test Org",
    token: "t",
    org: "org-1",
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

// ── Color-mode ────────────────────────────────────────────────────────────────
vi.mock("../theme/ColorModeContext", () => ({
  useColorMode: () => ({ mode: "light", toggle: vi.fn() }),
}));

// ── Force desktop layout so the permanent Drawer renders (no portal issues) ──
vi.mock("@mui/material/useMediaQuery", () => ({
  default: () => true,
}));

function renderLayout() {
  return render(
    <MemoryRouter>
      <Layout>
        <div>page content</div>
      </Layout>
    </MemoryRouter>
  );
}

// ── learner ───────────────────────────────────────────────────────────────────
describe("Layout nav — learner role", () => {
  beforeEach(() => {
    mockRole = "learner";
  });

  it("shows the Learn nav item", () => {
    renderLayout();
    expect(screen.getByRole("link", { name: /^learn$/i })).toBeInTheDocument();
  });

  it("shows the Dashboard nav item", () => {
    renderLayout();
    expect(screen.getByRole("link", { name: /^dashboard$/i })).toBeInTheDocument();
  });

  it("hides Course Builder", () => {
    renderLayout();
    expect(
      screen.queryByRole("link", { name: /course builder/i })
    ).not.toBeInTheDocument();
  });

  it("hides Dependency Graph", () => {
    renderLayout();
    expect(
      screen.queryByRole("link", { name: /dependency graph/i })
    ).not.toBeInTheDocument();
  });

  it("hides Changes", () => {
    renderLayout();
    expect(
      screen.queryByRole("link", { name: /^changes$/i })
    ).not.toBeInTheDocument();
  });

  it("hides Review", () => {
    renderLayout();
    expect(
      screen.queryByRole("link", { name: /^review$/i })
    ).not.toBeInTheDocument();
  });

  it("hides AI Inbox", () => {
    renderLayout();
    expect(
      screen.queryByRole("link", { name: /ai inbox/i })
    ).not.toBeInTheDocument();
  });

  it("hides the New Change Request button", () => {
    renderLayout();
    expect(
      screen.queryByRole("link", { name: /new change request/i })
    ).not.toBeInTheDocument();
  });
});

// ── architect ─────────────────────────────────────────────────────────────────
describe("Layout nav — architect role", () => {
  beforeEach(() => {
    mockRole = "architect";
  });

  it("shows Dashboard", () => {
    renderLayout();
    expect(screen.getByRole("link", { name: /^dashboard$/i })).toBeInTheDocument();
  });

  it("shows Learn", () => {
    renderLayout();
    expect(screen.getByRole("link", { name: /^learn$/i })).toBeInTheDocument();
  });

  it("shows Course Builder", () => {
    renderLayout();
    expect(
      screen.getByRole("link", { name: /course builder/i })
    ).toBeInTheDocument();
  });

  it("shows Dependency Graph", () => {
    renderLayout();
    expect(
      screen.getByRole("link", { name: /dependency graph/i })
    ).toBeInTheDocument();
  });

  it("shows Changes", () => {
    renderLayout();
    expect(
      screen.getByRole("link", { name: /^changes$/i })
    ).toBeInTheDocument();
  });

  it("shows Review", () => {
    renderLayout();
    expect(
      screen.getByRole("link", { name: /^review$/i })
    ).toBeInTheDocument();
  });

  it("shows AI Inbox", () => {
    renderLayout();
    expect(
      screen.getByRole("link", { name: /ai inbox/i })
    ).toBeInTheDocument();
  });

  it("shows the New Change Request button", () => {
    renderLayout();
    expect(
      screen.getByRole("link", { name: /new change request/i })
    ).toBeInTheDocument();
  });
});
