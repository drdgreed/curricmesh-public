"""Deterministic, rule-based intake helpers for draft items (Task 3).

NO AI. These are pure functions used when an author pastes/types raw content
and we want to (a) pre-fill a content ``kind`` and (b) infer cheap effort
metrics, all without a model call. Author-provided values always win over these
guesses (see ``router_course.py``); they exist only to make the empty-handed
paste case feel smart.

Every heuristic here is intentionally simple, documented inline, and pure so it
can be unit-tested branch-by-branch without a DB (see ``test_categorize.py``).
"""

from __future__ import annotations

import re

from app.models.enums import AssetKind

# Substrings that indicate a line is "code-ish". Kept deliberately small.
_CODE_TOKENS = (";", "{", "}", "(", ")", "=")
_CODE_PREFIXES = ("def ", "function ", "class ", "import ", "#include")

# (keyword, kind) in priority order. First match on the lowercased title (then
# text) wins, so more-specific kinds are listed before broad ones.
_KIND_KEYWORDS: tuple[tuple[str, AssetKind], ...] = (
    ("rubric", AssetKind.rubric),
    ("lab", AssetKind.lab),
    ("exercise", AssetKind.lab),
    ("project", AssetKind.project),
    ("slides", AssetKind.slides),
    ("deck", AssetKind.slides),
    ("slide", AssetKind.slides),
    ("quiz", AssetKind.assessment),
    ("assessment", AssetKind.assessment),
    ("exam", AssetKind.assessment),
    ("test", AssetKind.assessment),
    ("objective", AssetKind.learning_objectives),
    ("spec", AssetKind.spec),
    ("starter", AssetKind.starter),
    ("reference", AssetKind.references),
)


def _looks_like_code(non_empty: list[str]) -> bool:
    """True if ``non_empty`` lines look like source code.

    Rule: need at least 3 non-empty lines AND at least 30% of them are
    "code-ish" — i.e. contain one of ``; { } ( ) =``, start with leading
    indentation (a tab or two+ spaces), or start with a common keyword prefix
    (``def`` / ``function`` / ``class`` / ``import`` / ``#include``).
    """
    if len(non_empty) < 3:
        return False
    codeish = 0
    for line in non_empty:
        stripped = line.strip()
        if (
            any(tok in stripped for tok in _CODE_TOKENS)
            or line.startswith("\t")
            or line.startswith("  ")
            or any(stripped.startswith(p) for p in _CODE_PREFIXES)
        ):
            codeish += 1
    return codeish / len(non_empty) >= 0.30


def extract_metrics(text: str | None, source_url: str | None = None) -> dict:
    """Infer cheap effort metrics from pasted ``text``. Pure, no AI.

    Returns a dict with ONLY the keys it could compute (omits the rest):

    * ``word_count`` — ``len(text.split())`` whenever ``text`` is non-empty.
    * ``slide_count`` — if the text uses markdown slide breaks (lines that are
      exactly ``---``), this is the number of those separators **+ 1** (N
      separators delimit N+1 slides). Omitted when there are no such breaks.
    * ``lines_of_code`` — the number of non-empty lines, but only when the text
      "looks like code" per :func:`_looks_like_code`. Omitted otherwise.

    ``source_url`` is accepted for call-site symmetry (the router always passes
    it) but is not currently used to derive any metric — YAGNI until there's a
    real fetch step.
    """
    metrics: dict = {}
    if not text:
        return metrics

    metrics["word_count"] = len(text.split())

    lines = text.splitlines()
    slide_breaks = sum(1 for ln in lines if ln.strip() == "---")
    if slide_breaks:
        metrics["slide_count"] = slide_breaks + 1

    non_empty = [ln for ln in lines if ln.strip()]
    if _looks_like_code(non_empty):
        metrics["lines_of_code"] = len(non_empty)

    return metrics


def guess_kind(title: str, text: str | None = None) -> AssetKind:
    """Guess an :class:`AssetKind` from a title (then text) keyword scan.

    Scans the lowercased title first, then the lowercased text, against an
    ordered keyword table. First hit wins; defaults to ``lesson_plan`` when
    nothing matches.

    Matching is **word-boundary** safe: the haystack is tokenised into a set of
    lowercase words (``re.findall(r"[a-z]+", haystack)``) and each keyword must
    be present as a whole word. This prevents false positives such as "Latest
    Techniques" matching "test" → assessment, or "Collaborative" matching "lab"
    inside "collaborative".
    """
    haystacks = [title.lower()]
    if text:
        haystacks.append(text.lower())
    for haystack in haystacks:
        words = set(re.findall(r"[a-z]+", haystack))
        for keyword, kind in _KIND_KEYWORDS:
            # Match the keyword as a whole word OR its simple English plural
            # (+s) — e.g. "objectives" matches "objective", "references" matches
            # "reference". This keeps word-boundary safety while handling the
            # common singular/plural variation in course-title language.
            if keyword in words or keyword + "s" in words:
                return kind
    return AssetKind.lesson_plan
