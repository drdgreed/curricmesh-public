import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { StatusBadge } from "../components/StatusBadge";

describe("StatusBadge", () => {
  it("renders 'Draft' for draft status", () => {
    render(<StatusBadge status="draft" />);
    expect(screen.getByText("Draft")).toBeInTheDocument();
  });

  it("renders 'Active' for active status", () => {
    render(<StatusBadge status="active" />);
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("renders 'Review' for review status", () => {
    render(<StatusBadge status="review" />);
    expect(screen.getByText("Review")).toBeInTheDocument();
  });

  it("renders 'Approved' for approved status", () => {
    render(<StatusBadge status="approved" />);
    expect(screen.getByText("Approved")).toBeInTheDocument();
  });

  it("renders 'Sunset' for sunset status", () => {
    render(<StatusBadge status="sunset" />);
    expect(screen.getByText("Sunset")).toBeInTheDocument();
  });

  it("renders 'Archived' for archived status", () => {
    render(<StatusBadge status="archived" />);
    expect(screen.getByText("Archived")).toBeInTheDocument();
  });

  it("renders the raw status string for unknown statuses", () => {
    render(<StatusBadge status="unknown_state" />);
    expect(screen.getByText("unknown_state")).toBeInTheDocument();
  });

  it("sets the correct data-testid", () => {
    render(<StatusBadge status="active" />);
    expect(screen.getByTestId("status-badge-active")).toBeInTheDocument();
  });
});
