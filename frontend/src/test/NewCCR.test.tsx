import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { NewCCR } from "../pages/NewCCR";

// Mock the api/client module. ASSET_KINDS is imported as a value by NewCCR,
// so the mock must provide it.
vi.mock("../api/client", () => ({
  getDashboard: vi.fn(),
  createCCR: vi.fn(),
  ASSET_KINDS: [
    "lesson_plan",
    "slides",
    "assessment",
    "rubric",
    "lab",
    "spec",
    "starter",
    "references",
    "learning_objectives",
    "project",
  ],
  ASSET_KIND_LABELS: {
    lab: "Coding Lab",
    lesson_plan: "Lesson Plan",
    learning_objectives: "Learning Objectives",
    references: "References",
    starter: "Starter Code",
    project: "Project",
    slides: "Slides",
    assessment: "Assessment",
    rubric: "Rubric",
    spec: "Spec",
  },
  apiClient: { interceptors: { request: { use: vi.fn() } } },
}));

import { getDashboard, createCCR } from "../api/client";

// Controllable role for useAuth.
let mockRole = "architect";
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ role: mockRole, token: "t", org: "o", login: vi.fn(), logout: vi.fn() }),
}));

const singleCurriculum = {
  curricula: [
    {
      id: "cur1",
      name: "Data Engineering Bootcamp",
      slug: "data-eng",
      current_version_id: "v1",
      versions: [],
      cohorts: [],
      alignment: [],
    },
  ],
  recent_events: [],
};

function renderForm() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <NewCCR />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("NewCCR page — architect", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRole = "architect";
    vi.mocked(getDashboard).mockResolvedValue(singleCurriculum as never);
    vi.mocked(createCCR).mockResolvedValue({
      id: "new-ccr",
      curriculum_id: "cur1",
      author_id: "a1",
      title: "Refresh ML module",
      rationale: null,
      proposed_bump: "minor",
      external_link: null,
      impact: null,
      status: "draft",
      created_at: "2026-06-05T00:00:00Z",
    } as never);
  });

  it("renders the form fields", async () => {
    renderForm();
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /new change request/i })
      ).toBeInTheDocument();
    });
    expect(screen.getByLabelText(/title/i)).toBeInTheDocument();
  });

  it("disables submit when the title is empty", async () => {
    renderForm();
    const btn = await screen.findByRole("button", {
      name: /submit change request/i,
    });
    expect(btn).toBeDisabled();
  });

  it("disables submit when no affected kind is selected", async () => {
    renderForm();
    const titleInput = await screen.findByLabelText(/title/i);
    fireEvent.change(titleInput, { target: { value: "Refresh ML module" } });

    // Title is filled and a single curriculum resolves, but no affected_kind
    // is selected yet, so submit must remain disabled.
    const btn = screen.getByRole("button", { name: /submit change request/i });
    expect(btn).toBeDisabled();
  });

  it("calls createCCR with the right payload when the form is filled", async () => {
    renderForm();
    const titleInput = await screen.findByLabelText(/title/i);
    fireEvent.change(titleInput, { target: { value: "Refresh ML module" } });

    // Select an affected kind (now required by canSubmit).
    fireEvent.mouseDown(screen.getByLabelText(/affected kinds/i));
    const option = await screen.findByRole("option", { name: /lesson plan/i });
    fireEvent.click(option);
    // Close the listbox so it does not intercept the submit click.
    fireEvent.keyDown(option, { key: "Escape", code: "Escape" });

    const btn = screen.getByRole("button", { name: /submit change request/i });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);

    await waitFor(() => {
      expect(createCCR).toHaveBeenCalledWith({
        curriculum_id: "cur1",
        title: "Refresh ML module",
        proposed_bump: "minor",
        affected_kinds: ["lesson_plan"],
      });
    });
  });

  it("shows friendly option labels including Coding Lab and Project", async () => {
    renderForm();
    fireEvent.mouseDown(await screen.findByLabelText(/affected kinds/i));

    expect(
      await screen.findByRole("option", { name: /coding lab/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: /^project/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: /starter code/i })
    ).toBeInTheDocument();
  });

  it("exposes a kind-definitions help affordance", async () => {
    renderForm();
    const helpBtn = await screen.findByRole("button", {
      name: /affected kind definitions/i,
    });
    expect(helpBtn).toBeInTheDocument();
  });

  it("surfaces a 403 API error on failure", async () => {
    const err = new AxiosError("Forbidden");
    err.response = {
      status: 403,
      data: { detail: "Not allowed for your role" },
    } as never;
    vi.mocked(createCCR).mockRejectedValueOnce(err);

    renderForm();
    const titleInput = await screen.findByLabelText(/title/i);
    fireEvent.change(titleInput, { target: { value: "Refresh ML module" } });

    fireEvent.mouseDown(screen.getByLabelText(/affected kinds/i));
    const option = await screen.findByRole("option", { name: /lesson plan/i });
    fireEvent.click(option);
    fireEvent.keyDown(option, { key: "Escape", code: "Escape" });

    const btn = screen.getByRole("button", { name: /submit change request/i });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByText(/Not allowed for your role/i)).toBeInTheDocument();
    });
  });
});

describe("NewCCR page — instructor (cannot author)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRole = "instructor";
    vi.mocked(getDashboard).mockResolvedValue(singleCurriculum as never);
  });

  it("shows a not-permitted warning instead of the form", async () => {
    renderForm();
    await waitFor(() => {
      expect(screen.getByText(/not permitted to author/i)).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: /submit change request/i })
    ).not.toBeInTheDocument();
  });
});
