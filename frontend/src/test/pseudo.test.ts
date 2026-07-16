/**
 * Unit coverage for the pseudolocalization + overflow modules (jsdom).
 *
 * jsdom has no layout engine (scrollWidth is always 0), so the overflow
 * detector's real behavior is exercised by the Playwright regime in
 * tests/e2e/. Here we verify (a) the pseudo DOM transform, which is pure DOM
 * manipulation jsdom fully supports, and (b) that the detector runs cleanly and
 * returns the right shape against a laid-out-free DOM.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { applyPseudo } from "../../tests/pseudo/pseudo";
import { detectOverflow } from "../../tests/pseudo/overflow";

beforeEach(() => {
  document.documentElement.removeAttribute("data-pseudo");
  document.documentElement.removeAttribute("dir");
  document.body.innerHTML = "";
});

describe("applyPseudo — expand", () => {
  it("brackets and lengthens visible text", () => {
    document.body.innerHTML = `<p id="t">Course Builder</p>`;
    applyPseudo("expand");
    const text = document.getElementById("t")!.textContent!;
    expect(text.startsWith("⟦")).toBe(true);
    expect(text.endsWith("⟧")).toBe(true);
    expect(text.length).toBeGreaterThan("Course Builder".length);
    // padded to ~140%: 13 visible chars -> +ceil(13*0.4)=+6 pad dots minimum
    expect(text).toContain("·");
  });

  it("accents ascii letters", () => {
    document.body.innerHTML = `<span id="t">abc</span>`;
    applyPseudo("expand");
    expect(document.getElementById("t")!.textContent).toContain("á");
  });

  it("skips script/style/code subtrees", () => {
    document.body.innerHTML = `<code id="c">keepme</code><script id="s">alsokeep</script>`;
    applyPseudo("expand");
    expect(document.getElementById("c")!.textContent).toBe("keepme");
    expect(document.getElementById("s")!.textContent).toBe("alsokeep");
  });

  it("is idempotent per navigation", () => {
    document.body.innerHTML = `<p id="t">Hello</p>`;
    applyPseudo("expand");
    const once = document.getElementById("t")!.textContent;
    applyPseudo("expand");
    expect(document.getElementById("t")!.textContent).toBe(once);
  });

  it("baseline is a no-op", () => {
    document.body.innerHTML = `<p id="t">Hello</p>`;
    applyPseudo("baseline");
    expect(document.getElementById("t")!.textContent).toBe("Hello");
  });
});

describe("applyPseudo — rtl", () => {
  it("sets document direction and bidi-wraps text", () => {
    document.body.innerHTML = `<p id="t">Dashboard</p>`;
    applyPseudo("rtl");
    expect(document.documentElement.getAttribute("dir")).toBe("rtl");
    const text = document.getElementById("t")!.textContent!;
    expect(text).toContain("Dashboard");
    expect(text.length).toBeGreaterThan("Dashboard".length); // wrapped
  });
});

describe("detectOverflow", () => {
  it("returns an array and does not throw on an empty document", () => {
    const offenders = detectOverflow();
    expect(Array.isArray(offenders)).toBe(true);
  });

  it("returns offenders with {selector, kind, text} shape when present", () => {
    document.body.innerHTML = `<div id="x">content</div>`;
    const offenders = detectOverflow();
    for (const o of offenders) {
      expect(o).toHaveProperty("selector");
      expect(o).toHaveProperty("kind");
      expect(o).toHaveProperty("text");
    }
  });
});
