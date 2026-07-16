/**
 * Pseudolocalization engine (T1).
 *
 * There is NO i18n framework in this app. To surface translation *effects*
 * (text expansion, RTL mirroring) we transform the already-rendered DOM at
 * test time. `applyPseudo` is injected into the page by Playwright via
 * `page.evaluate(applyPseudo, mode)`, so it MUST be fully self-contained — it
 * may not reference module-scope imports or outer closures (Playwright
 * serializes it with `Function.prototype.toString`). Everything it needs is
 * declared inside its body.
 *
 * The same function is import-and-called directly in a jsdom vitest unit test,
 * which is why it lives in a module rather than an inline string.
 */

export type PseudoMode = "baseline" | "expand" | "rtl";

/**
 * Transform every visible text node in the current document for the given mode.
 *
 * - `baseline`: no-op (renders the app as-is).
 * - `expand`:   accent each letter, pad to ~140% length, wrap in ⟦ ⟧ brackets.
 *               This is what drives expansion-driven layout overflow.
 * - `rtl`:      set <html dir="rtl"> and bidi-wrap each text node.
 *
 * Skips <script>/<style>/<noscript>/<code>/<pre>/<textarea> subtrees and
 * contenteditable regions. Input *values* are attributes, not text nodes, so
 * they are naturally untouched. Idempotent per navigation via a flag on <html>.
 */
export function applyPseudo(mode: PseudoMode): void {
  if (mode === "baseline") return;

  const html = document.documentElement;
  if (html.getAttribute("data-pseudo") === mode) return; // already applied
  html.setAttribute("data-pseudo", mode);

  const OPEN = "⟦"; // ⟦
  const CLOSE = "⟧"; // ⟧
  const PAD = "·"; // · middle dot, used to pad expansion
  const RLE = "‫"; // right-to-left embedding
  const PDF = "‬"; // pop directional formatting

  // A→accented map covering the ASCII letters. Non-letters pass through.
  const ACCENTS: Record<string, string> = {
    a: "á", b: "ƀ", c: "ç", d: "đ", e: "é",
    f: "ƒ", g: "ĝ", h: "ĥ", i: "î", j: "ĵ",
    k: "ķ", l: "ļ", m: "ḿ", n: "ñ", o: "ô",
    p: "þ", q: "ǫ", r: "ŕ", s: "š", t: "ţ",
    u: "û", v: "ṽ", w: "ŵ", x: "ẋ", y: "ý",
    z: "ž",
    A: "Å", B: "ß", C: "Ç", D: "Ď", E: "É",
    F: "Ƒ", G: "Ĝ", H: "Ĥ", I: "Î", J: "Ĵ",
    K: "Ķ", L: "Ļ", M: "Ḿ", N: "Ñ", O: "Ô",
    P: "Þ", Q: "Ǫ", R: "Ŕ", S: "Š", T: "Ţ",
    U: "Û", V: "Ṽ", W: "Ŵ", X: "Ẋ", Y: "Ý",
    Z: "Ž",
  };

  function accent(s: string): string {
    let out = "";
    for (const ch of s) out += ACCENTS[ch] ?? ch;
    return out;
  }

  function expand(s: string): string {
    // Preserve leading/trailing whitespace so inline spacing is not destroyed.
    const lead = s.match(/^\s*/)?.[0] ?? "";
    const trail = s.match(/\s*$/)?.[0] ?? "";
    const core = s.slice(lead.length, s.length - trail.length);
    if (core.length === 0) return s;
    // Pad by ~40% of the visible (non-space) length to model expansion.
    const visible = core.replace(/\s/g, "").length;
    const padCount = Math.max(1, Math.ceil(visible * 0.4));
    return lead + OPEN + accent(core) + PAD.repeat(padCount) + CLOSE + trail;
  }

  function transform(s: string): string {
    if (s.trim().length === 0) return s; // whitespace-only: leave alone
    if (s.indexOf(OPEN) !== -1 || s.indexOf(RLE) !== -1) return s; // already done
    return mode === "expand" ? expand(s) : RLE + s + PDF;
  }

  if (mode === "rtl") html.setAttribute("dir", "rtl");

  const SKIP_TAGS = new Set([
    "SCRIPT", "STYLE", "NOSCRIPT", "CODE", "PRE", "TEXTAREA", "SVG",
  ]);

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node: Node): number {
      const text = node.nodeValue ?? "";
      if (text.trim().length === 0) return NodeFilter.FILTER_REJECT;
      let el: Node | null = node.parentNode;
      while (el && el.nodeType === 1) {
        const tag = (el as Element).tagName;
        if (SKIP_TAGS.has(tag)) return NodeFilter.FILTER_REJECT;
        if ((el as HTMLElement).isContentEditable) return NodeFilter.FILTER_REJECT;
        el = el.parentNode;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });

  const targets: Text[] = [];
  let cur = walker.nextNode();
  while (cur) {
    targets.push(cur as Text);
    cur = walker.nextNode();
  }
  for (const node of targets) {
    node.nodeValue = transform(node.nodeValue ?? "");
  }
}
