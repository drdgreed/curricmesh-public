/**
 * Accepted baseline of CURRENT-STATE layout offenders (T1).
 *
 * The overflow detector is a hard regression gate: any NEW offender fails the
 * suite. The entries below are genuine findings that already exist in the UI
 * today (surfaced by this harness on its first run). They are recorded here —
 * reviewable in the diff — as tech debt for a FOLLOW-UP slice, NOT fixed in T1
 * (whose job is only to surface them). The gate stays green today and turns red
 * the moment translation introduces a *new* overflow.
 *
 * Matching ignores volatile ids (React `useId`, MUI-generated) so an entry is
 * `{ kind, selector-without-#id }`. Keyed by `"<screen>:<mode>"`.
 *
 * All three T1 findings have been fixed in the follow-up slice (fix/layout-overflow-offenders):
 *   1. Org name (`p.MuiTypography-body2`, noWrap) — `Layout.tsx` sidebar footer:
 *      removed `noWrap`, added `title` attribute; text now wraps within the 248px drawer.
 *   2. Course Builder active horizontal scrollbar (1524 > 1280px) — `CourseBuilder.tsx`
 *      outer grid changed `lg` → `xl`; the 3-col ObjectiveCanvas canvas now has ≥984px
 *      at the 1280px test viewport instead of the ~36px centre column that forced overflow.
 *   3. Media filename + Bloom-level select truncation — `MediaLibraryPanel.tsx` filename
 *      Typography: removed `noWrap`, added `overflowWrap:anywhere`; `ObjectiveCanvas.tsx`
 *      Bloom Select: separated from the Week field into its own full-width row, giving the
 *      select ~268px instead of ~164px so translated values fit without ellipsis.
 */

export interface KnownOffender {
  kind: string;
  /** Selector with any `#id` fragments stripped (ids are non-deterministic). */
  selector: string;
}

/**
 * T2 (content-layout.spec.ts) — accepted baseline of CURRENT-STATE *content*
 * overflow.
 *
 * FINDING (real, pre-existing app bug surfaced by T2): none of the content
 * renderers set `overflow-wrap: anywhere` / `word-break: break-word`, so a long
 * UNBROKEN token in authored content — a URL with no break opportunities, or a
 * pasted long identifier — does not wrap and forces a horizontal PAGE scrollbar
 * (`page-horizontal-scroll` on `html`). It reproduces across every content
 * surface:
 *   • Course Builder — `ObjectiveCanvas` objective text (`.MuiTypography-body2`)
 *     and item titles, in the 300px objective rail.
 *   • Course Player — `AssetContent` markdown paragraphs (`MarkdownView`),
 *     objective bullets (`Bullets`), and rubric rows (`RubricView`) via
 *     `LessonContent`.
 * Native CJK (Japanese, no spaces) and RTL (Arabic) wrap correctly — those
 * `player-cjk` / `player-rtl` baselines PASS, so the gap is specifically
 * unbroken-token wrapping, not script support.
 *
 * FOLLOW-UP (do NOT fix here — that's an app-layout slice): add
 * `overflowWrap: "anywhere"` (and for the URL case `wordBreak: "break-word"`) to
 * the shared text renderers — `AssetContent` (`MarkdownView`/`Bullets`/
 * `RubricView`) and the builder `ObjectiveCard`/`ItemCard` typography. Removing
 * the wrap-anywhere gap should let every entry below be deleted and the gate
 * turn red only on genuine *new* content overflow.
 *
 * Matching ignores volatile ids; the `html` selector is fully stable.
 */
const CONTENT_PAGE_SCROLL: KnownOffender[] = [
  { kind: "page-horizontal-scroll", selector: "html" },
];

export const KNOWN_OFFENDERS: Record<string, KnownOffender[]> = {
  // --- T1 chrome: all offenders fixed; any new chrome overflow fails CI. ---

  // --- T2 content: unbroken-token → page horizontal scroll (see note above). ---
  "builder-content:baseline": CONTENT_PAGE_SCROLL,
  "builder-content:expand": CONTENT_PAGE_SCROLL,
  "builder-content:rtl": CONTENT_PAGE_SCROLL,
  "player-markdown:baseline": CONTENT_PAGE_SCROLL,
  "player-markdown:expand": CONTENT_PAGE_SCROLL,
  "player-markdown:rtl": CONTENT_PAGE_SCROLL,
  "player-objectives:baseline": CONTENT_PAGE_SCROLL,
  "player-objectives:expand": CONTENT_PAGE_SCROLL,
  "player-objectives:rtl": CONTENT_PAGE_SCROLL,
  "player-rubric:baseline": CONTENT_PAGE_SCROLL,
  "player-rubric:expand": CONTENT_PAGE_SCROLL,
  "player-rubric:rtl": CONTENT_PAGE_SCROLL,
  "player-assessment:baseline": CONTENT_PAGE_SCROLL,
  "player-assessment:expand": CONTENT_PAGE_SCROLL,
  "player-assessment:rtl": CONTENT_PAGE_SCROLL,
};

/** Strip non-deterministic `#id` fragments so selectors compare stably. */
export function normalizeSelector(selector: string): string {
  return selector.replace(/#[^.[]+/g, "");
}
