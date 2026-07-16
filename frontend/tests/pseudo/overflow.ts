/**
 * Layout-overflow detector (T1) — the deterministic HARD GATE.
 *
 * jsdom cannot measure layout (scrollWidth/clientWidth are always 0), so this
 * audit must run inside a real browser. `detectOverflow` is injected into the
 * page by Playwright via `page.evaluate(detectOverflow)` and therefore MUST be
 * fully self-contained (no module-scope imports / outer closures).
 *
 * It flags four deterministic symptoms of text that no longer fits its box —
 * the failure mode translated (expanded) copy produces:
 *
 *   - `page-horizontal-scroll` : the document itself scrolls sideways.
 *   - `clipped-x`              : text-bearing box with overflow-x hidden/clip
 *                               whose content is wider than the box (text lost).
 *   - `truncated-ellipsis`     : an ellipsis-truncated label that is actually
 *                               being truncated (translated text cut off).
 *   - `clipped-y`             : text-bearing box with overflow-y hidden whose
 *                               content is taller than the box (rows lost).
 *
 * Scroll containers (overflow auto/scroll) are intentional and NOT reported.
 */

export interface Offender {
  selector: string;
  kind: string;
  text: string;
}

export function detectOverflow(): Offender[] {
  const TOL = 1; // px tolerance for sub-pixel rounding
  const out: Offender[] = [];
  const seen = new Set<string>();

  function shortText(el: Element): string {
    const t = (el.textContent ?? "").replace(/\s+/g, " ").trim();
    return t.length > 80 ? t.slice(0, 77) + "…" : t;
  }

  function selectorFor(el: Element): string {
    const tag = el.tagName.toLowerCase();
    const id = (el as HTMLElement).id ? `#${(el as HTMLElement).id}` : "";
    const cls =
      typeof el.className === "string" && el.className.trim()
        ? "." +
          el.className
            .trim()
            .split(/\s+/)
            .slice(0, 2)
            .join(".")
        : "";
    const testid = el.getAttribute("data-testid");
    const tid = testid ? `[data-testid="${testid}"]` : "";
    return `${tag}${id}${tid}${cls}`;
  }

  function hasDirectText(el: Element): boolean {
    for (const n of Array.from(el.childNodes)) {
      if (n.nodeType === 3 && (n.nodeValue ?? "").trim().length > 0) return true;
    }
    return false;
  }

  function push(el: Element, kind: string) {
    const sel = selectorFor(el);
    const key = `${kind}::${sel}::${shortText(el)}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ selector: sel, kind, text: shortText(el) });
  }

  // 1. Page-level horizontal scrollbar.
  const de = document.documentElement;
  if (de.scrollWidth > de.clientWidth + TOL) {
    out.push({
      selector: "html",
      kind: "page-horizontal-scroll",
      text: `scrollWidth=${de.scrollWidth} clientWidth=${de.clientWidth}`,
    });
    seen.add("page");
  }

  // 2..4. Per-element clipping / truncation of text-bearing boxes.
  const all = document.body.querySelectorAll("*");
  for (const el of Array.from(all)) {
    const tag = el.tagName;
    if (
      tag === "SCRIPT" ||
      tag === "STYLE" ||
      tag === "svg" ||
      tag === "SVG" ||
      tag === "path" ||
      tag === "IMG"
    ) {
      continue;
    }
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue; // not rendered
    if (!hasDirectText(el)) continue; // only text-bearing boxes

    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none") continue;

    // scrollWidth/clientWidth are measured in the element's *pre-transform*
    // coordinate space. Scaled elements (notably MUI's shrunk <InputLabel>,
    // which applies `scale(0.75)` + a 133% width hack) report a spurious
    // width mismatch that is not a user-visible truncation. Skip them — real
    // chrome text (nav labels, headings, org name) is never transform-scaled.
    if (cs.transform !== "none") continue;

    const overX = cs.overflowX;
    const overY = cs.overflowY;

    // truncated-ellipsis: ellipsis is active AND content really is wider.
    if (
      cs.textOverflow === "ellipsis" &&
      el.scrollWidth > el.clientWidth + TOL
    ) {
      push(el, "truncated-ellipsis");
      continue;
    }

    // clipped-x: content lost because the box hides horizontal overflow.
    if (
      (overX === "hidden" || overX === "clip") &&
      el.scrollWidth > el.clientWidth + TOL
    ) {
      push(el, "clipped-x");
      continue;
    }

    // clipped-y: rows lost because the box hides vertical overflow.
    if (
      (overY === "hidden" || overY === "clip") &&
      el.scrollHeight > el.clientHeight + TOL
    ) {
      push(el, "clipped-y");
    }
  }

  return out;
}
