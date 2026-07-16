import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock the builder API — generateCourse (schedules a job) + getGenerationJob
// (poll). The component under test owns the POST → poll → navigate flow.
vi.mock("../api/builder", () => ({
  generateCourse: vi.fn(),
  getGenerationJob: vi.fn(),
}));

import { GenerateCourseStep } from "../pages/builder/GenerateCourseStep";
import { generateCourse, getGenerationJob } from "../api/builder";

function renderStep(onCreated: (id: string) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <GenerateCourseStep onCreated={onCreated} />
    </QueryClientProvider>
  );
}

function fillAndSubmit() {
  fireEvent.change(screen.getByTestId("generate-course-title"), {
    target: { value: "AI Engineering 101" },
  });
  fireEvent.change(screen.getByTestId("generate-course-topic"), {
    target: { value: "Building tool-using agents" },
  });
  fireEvent.click(screen.getByTestId("generate-course-btn"));
}

describe("GenerateCourseStep — async generate → poll → navigate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("POSTs the brief, shows progress, then hands the course id to onCreated on complete", async () => {
    vi.mocked(generateCourse).mockResolvedValue({ job_id: "job-1" });
    // First poll: running (progress). Then: complete with the course id.
    vi.mocked(getGenerationJob)
      .mockResolvedValueOnce({
        job_id: "job-1",
        status: "running",
        completed_steps: 3,
        total_steps: 9,
        phase: "Lesson 1/4",
        course_id: null,
        error: null,
      })
      .mockResolvedValue({
        job_id: "job-1",
        status: "complete",
        completed_steps: 9,
        total_steps: 9,
        phase: "complete",
        course_id: "course-xyz",
        error: null,
      });

    const onCreated = vi.fn();
    renderStep(onCreated);
    fillAndSubmit();

    // The brief was scheduled (POST), not run inline.
    await waitFor(() => expect(generateCourse).toHaveBeenCalled());
    expect(vi.mocked(generateCourse).mock.calls[0][0]).toMatchObject({
      title: "AI Engineering 101",
      topic: "Building tool-using agents",
    });

    // Live progress from the first poll is shown.
    const progress = await screen.findByTestId("generate-course-progress");
    expect(progress).toHaveTextContent("3/9");
    expect(progress).toHaveTextContent("Lesson 1/4");

    // On completion the new draft course id is handed to the shell.
    await waitFor(
      () => expect(onCreated).toHaveBeenCalledWith("course-xyz"),
      { timeout: 4000 }
    );
  });

  it("shows the error and a Retry when the job fails; never calls onCreated", async () => {
    vi.mocked(generateCourse).mockResolvedValue({ job_id: "job-2" });
    vi.mocked(getGenerationJob).mockResolvedValue({
      job_id: "job-2",
      status: "failed",
      completed_steps: 1,
      total_steps: 9,
      phase: "objectives",
      course_id: null,
      error: "model refused the objectives",
    });

    const onCreated = vi.fn();
    renderStep(onCreated);
    fillAndSubmit();

    const alert = await screen.findByText(/Generation failed/i);
    expect(alert).toHaveTextContent("model refused the objectives");
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    expect(onCreated).not.toHaveBeenCalled();
  });
});
