import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock the builder API layer. The three generators + their write fns
// (addObjective / updateItem / addItem+alignItem) are the surface under test;
// isAiNotConfigured keeps its real 503-detection so the graceful-degradation
// branch is exercised faithfully.
vi.mock("../api/builder", () => ({
  BLOOM_LEVELS: [
    "remember",
    "understand",
    "apply",
    "analyze",
    "evaluate",
    "create",
  ],
  listObjectives: vi.fn(),
  listItems: vi.fn(),
  getOverload: vi.fn(),
  listItemMedia: vi.fn(),
  addObjective: vi.fn(),
  addItem: vi.fn(),
  alignItem: vi.fn(),
  attachMedia: vi.fn(),
  detachMedia: vi.fn(),
  categorizeItemAI: vi.fn(),
  updateItem: vi.fn(),
  generateObjectives: vi.fn(),
  generateItemContent: vi.fn(),
  generateAssessment: vi.fn(),
  isAiNotConfigured: (e: unknown) =>
    (e as { response?: { status?: number } })?.response?.status === 503,
}));

vi.mock("../api/media", () => ({
  listReadyMedia: vi.fn(),
}));

import { ObjectiveCanvas } from "../pages/builder/ObjectiveCanvas";
import type { CourseOut, ItemOut, ObjectiveOut } from "../api/builder";
import {
  addItem,
  addObjective,
  alignItem,
  generateAssessment,
  generateItemContent,
  generateObjectives,
  getOverload,
  listItemMedia,
  listItems,
  listObjectives,
  updateItem,
} from "../api/builder";
import { listReadyMedia } from "../api/media";

const course: CourseOut = {
  id: "course-1",
  title: "Intro to Agents",
  description: null,
  learner_profile: null,
  effort_config: null,
  target_weeks: 4,
  status: "draft",
  curriculum_id: null,
  created_at: "2026-01-01T00:00:00Z",
};

const objective: ObjectiveOut = {
  id: "obj-1",
  draft_course_id: "course-1",
  text: "Build a tool-using agent",
  bloom_level: "apply",
  key_skills: ["tool use"],
  week_index: 1,
  order_index: 0,
};

const item: ItemOut = {
  id: "item-1",
  draft_course_id: "course-1",
  kind: "lesson_plan",
  title: "Agents 101",
  content: null,
  source_url: null,
  metrics: null,
  week_index: 1,
  order_index: 0,
  estimated_minutes: 30,
};

function renderCanvas() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ObjectiveCanvas course={course} />
    </QueryClientProvider>
  );
}

describe("ObjectiveCanvas — AI per-aspect generators", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listObjectives).mockResolvedValue([objective]);
    vi.mocked(listItems).mockResolvedValue([item]);
    vi.mocked(getOverload).mockResolvedValue([]);
    vi.mocked(listItemMedia).mockResolvedValue([]);
    vi.mocked(listReadyMedia).mockResolvedValue([]);
  });

  it("Generate objectives → draft shows → Add writes a DraftObjective via addObjective", async () => {
    vi.mocked(generateObjectives).mockResolvedValue({
      objectives: [
        {
          text: "Explain the agent loop",
          bloom_level: "understand",
          key_skills: ["reasoning"],
        },
      ],
    });
    vi.mocked(addObjective).mockResolvedValue(objective);

    renderCanvas();

    fireEvent.click(await screen.findByTestId("generate-objectives-btn"));

    await waitFor(() =>
      expect(generateObjectives).toHaveBeenCalledWith("course-1")
    );
    const draft = await screen.findByTestId("generated-objective");
    expect(draft).toHaveTextContent("Explain the agent loop");

    fireEvent.click(screen.getByTestId("generated-objective-add"));

    await waitFor(() =>
      expect(addObjective).toHaveBeenCalledWith("course-1", {
        text: "Explain the agent loop",
        bloom_level: "understand",
        key_skills: ["reasoning"],
      })
    );
  });

  it("Generate content → draft + caveats show → Apply writes into the item via updateItem", async () => {
    vi.mocked(generateItemContent).mockResolvedValue({
      kind: "lesson_plan",
      content_markdown: "# Agents 101\n\nA lesson body.",
      summary: "Covers the agent loop.",
      caveats: ["Verify the framework version."],
    });
    vi.mocked(updateItem).mockResolvedValue(item);

    renderCanvas();

    fireEvent.click(await screen.findByTestId("item-generate-content-btn"));

    await waitFor(() =>
      expect(generateItemContent).toHaveBeenCalledWith("item-1")
    );
    const draft = await screen.findByTestId("item-content-draft");
    expect(draft).toHaveTextContent("A lesson body.");
    // Caveats surfaced to the author.
    expect(screen.getByTestId("ai-caveats")).toHaveTextContent(
      "Verify the framework version."
    );

    fireEvent.click(screen.getByTestId("item-content-apply-btn"));

    await waitFor(() =>
      expect(updateItem).toHaveBeenCalledWith("item-1", {
        content: "# Agents 101\n\nA lesson body.",
      })
    );
  });

  it("Generate assessment → draft shows → Apply creates an aligned assessment item", async () => {
    vi.mocked(generateAssessment).mockResolvedValue({
      content_markdown: "## Quiz\n\nQ1. What is a tool?",
      rubric: "Full credit: correct + justified.",
      caveats: [],
    });
    vi.mocked(addItem).mockResolvedValue({ ...item, id: "item-2" });
    vi.mocked(alignItem).mockResolvedValue(undefined);

    renderCanvas();

    fireEvent.click(
      await screen.findByTestId("objective-generate-assessment-btn")
    );

    await waitFor(() =>
      expect(generateAssessment).toHaveBeenCalledWith("obj-1")
    );
    const draft = await screen.findByTestId("assessment-draft");
    expect(draft).toHaveTextContent("What is a tool?");

    fireEvent.click(screen.getByTestId("assessment-apply-btn"));

    await waitFor(() => expect(addItem).toHaveBeenCalledTimes(1));
    const [courseArg, body] = vi.mocked(addItem).mock.calls[0];
    expect(courseArg).toBe("course-1");
    expect(body.kind).toBe("assessment");
    expect(body.content).toContain("What is a tool?");
    expect(body.content).toContain("Full credit");
    // The new assessment item is linked to the objective it measures.
    await waitFor(() =>
      expect(alignItem).toHaveBeenCalledWith("item-2", "obj-1")
    );
  });

  it("surfaces the not-configured notice when a generator 503s", async () => {
    vi.mocked(generateObjectives).mockRejectedValue({
      response: { status: 503 },
    });

    renderCanvas();

    fireEvent.click(await screen.findByTestId("generate-objectives-btn"));

    expect(
      await screen.findByTestId("objectives-ai-not-configured")
    ).toBeInTheDocument();
  });
});
