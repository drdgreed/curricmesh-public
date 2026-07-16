import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { renderInline, AssetContent } from "../components/AssetContent";

describe("renderInline", () => {
  it("renders **bold** as <strong> and drops the asterisks", () => {
    render(<div>{renderInline("Duration: **90 minutes** total")}</div>);
    const strong = screen.getByText("90 minutes");
    expect(strong.tagName).toBe("STRONG");
    expect(document.body.textContent).not.toContain("**");
  });

  it("renders `code` as a <code> element", () => {
    render(<div>{renderInline("call `get_weather()` first")}</div>);
    const code = screen.getByText("get_weather()");
    expect(code.tagName).toBe("CODE");
  });

  it("passes plain text through unchanged", () => {
    render(<div>{renderInline("just plain words")}</div>);
    expect(screen.getByText("just plain words")).toBeTruthy();
  });
});

describe("AssetContent markdown", () => {
  it("renders ### as a heading and **bold** within lesson text", () => {
    const md = "### Lesson Overview\nThis lesson is **~90 minutes** long.";
    render(<AssetContent kind="lesson_plan" content={md} />);
    expect(screen.getByText("Lesson Overview")).toBeTruthy();
    expect(screen.getByText("~90 minutes").tagName).toBe("STRONG");
    // the literal markdown markers must not survive
    expect(document.body.textContent).not.toContain("###");
    expect(document.body.textContent).not.toContain("**");
  });
});
