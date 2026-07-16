"""Pure metric functions for the C4 AI evaluation harness.

Everything here is deterministic and side-effect-free (no I/O, no model calls),
so it is unit-tested with crafted inputs. The harness (``run_eval``) feeds these
functions the outputs of the ``GapExtractor`` / ``QAJudge`` seams.

Matching rule (mirrors PLANTED_GAPS.json ``_meta``): a planted gap is "hit" if
its full topic name OR any canonical tag OR any parenthetical abbreviation
appears in a found topic's text. Long terms match as case-insensitive
substrings; SHORT terms (abbreviations like "MCP", "DPO", "SFT") match only on
word boundaries, so "SFT" does not spuriously hit "crafting".
"""

from __future__ import annotations

import re
from itertools import combinations

# Terms at or below this length must match on a word boundary, not as a bare
# substring — otherwise short abbreviations produce false hits ("SFT" in
# "crafting", "DPO" in "endpoint"-like words, etc.).
_SHORT_TERM_LEN = 4


def build_match_terms(gap: dict) -> set[str]:
    """Lowercased set of strings that, if present in text, mean this gap is hit.

    Includes the gap's ``topic``, every ``canonical_tag``, and any parenthetical
    abbreviation extracted from those strings (e.g. "Model Context Protocol
    (MCP)" contributes "mcp").
    """
    raw: list[str] = [gap.get("topic", "")]
    raw.extend(gap.get("canonical_tags", []))

    terms: set[str] = set()
    for s in raw:
        if not s:
            continue
        terms.add(s.lower().strip())
        # Pull any parenthetical abbreviation, e.g. "... (MCP)" -> "mcp".
        for abbrev in re.findall(r"\(([^)]+)\)", s):
            abbrev = abbrev.strip().lower()
            if abbrev:
                terms.add(abbrev)
    return {t for t in terms if t}


def topic_hits(text: str, match_terms: set[str]) -> bool:
    """True if any match term appears in ``text`` (case-insensitive).

    Short terms (<= ``_SHORT_TERM_LEN`` chars, i.e. abbreviations) match only on
    a word boundary to avoid false hits inside longer words.
    """
    if not text:
        return False
    haystack = text.lower()
    for term in match_terms:
        if not term:
            continue
        if len(term) <= _SHORT_TERM_LEN:
            if re.search(rf"\b{re.escape(term)}\b", haystack):
                return True
        elif term in haystack:
            return True
    return False


def precision_recall(found_topics: list[str], planted_gaps: list[dict]) -> dict:
    """Score found gap topics against the planted ground-truth gaps.

    Matching is many-to-one at the gap level: a found topic that hits no gap is
    a false positive; a gap hit by >= 1 found topic is a true positive; an unhit
    gap is a false negative. Each gap is counted at most once.

    Returns precision/recall, the raw tp/fp/fn counts, the lists of matched /
    missed gaps and false-positive topics, and ``recall_by_signal_strength``
    (recall within each gap's ``signal_strength`` bucket — surfaces recall at
    lower-signal gaps).
    """
    gap_terms = [(g, build_match_terms(g)) for g in planted_gaps]

    hit_gap_idx: set[int] = set()
    false_positives: list[str] = []

    for found in found_topics:
        matched_any = False
        for idx, (_gap, terms) in enumerate(gap_terms):
            if topic_hits(found, terms):
                hit_gap_idx.add(idx)
                matched_any = True
        if not matched_any:
            false_positives.append(found)

    matched_gaps = [planted_gaps[i]["topic"] for i in sorted(hit_gap_idx)]
    missed_gaps = [
        g["topic"] for i, g in enumerate(planted_gaps) if i not in hit_gap_idx
    ]

    tp = len(hit_gap_idx)
    fp = len(false_positives)
    fn = len(planted_gaps) - tp

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    # Recall within each signal-strength bucket.
    buckets: dict[str, list[bool]] = {}
    for idx, gap in enumerate(planted_gaps):
        strength = gap.get("signal_strength", "unknown")
        buckets.setdefault(strength, []).append(idx in hit_gap_idx)
    recall_by_signal_strength = {
        strength: (sum(hits) / len(hits) if hits else 0.0)
        for strength, hits in buckets.items()
    }

    return {
        "precision": precision,
        "recall": recall,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "matched_gaps": matched_gaps,
        "missed_gaps": missed_gaps,
        "false_positives": false_positives,
        "recall_by_signal_strength": recall_by_signal_strength,
    }


def qa_agreement(
    ai_scores: dict[str, int], human_scores: dict[str, int], tol: int = 1
) -> dict:
    """Agreement between AI and human QA scores over every human dimension.

    A dimension agrees if ``abs(ai - human) <= tol``. The denominator is the
    full set of ``human_scores`` dimensions — this function does NOT silently
    pass over dimensions the AI failed to score. If any dimension present in
    ``human_scores`` is missing from ``ai_scores`` it raises ``ValueError``.
    Returns the agreement fraction, the count of dimensions, the
    within-tolerance count, and the per-dimension boolean map. Empty input
    (``{}``, ``{}``) yields agreement 0.0 with n == 0 and does NOT raise.
    """
    missing = [d for d in human_scores if d not in ai_scores]
    if missing:
        raise ValueError(f"AI scores missing dimensions: {sorted(missing)}")
    dims = list(human_scores)
    per_dimension = {
        d: abs(ai_scores[d] - human_scores[d]) <= tol for d in dims
    }
    within_tol = sum(per_dimension.values())
    n = len(dims)
    agreement = within_tol / n if n else 0.0
    return {
        "agreement": agreement,
        "n": n,
        "within_tol": within_tol,
        "per_dimension": per_dimension,
    }


# ---------------------------------------------------------------------------
# Multi-rater QA primitives (richer eval: consensus + human inter-rater baseline)
# ---------------------------------------------------------------------------


def consensus_scores(rater_scores: dict[str, dict[str, int]]) -> dict[str, int]:
    """Per-dimension median of the raters' scores (the rater "consensus").

    Dimensions are the union of every rater's dimension keys. For each
    dimension, collect the score from every rater that has it, sort, and take
    the median. Odd count -> the middle value. Even count -> the lower-middle
    value deterministically, i.e. ``sorted_vals[(len - 1) // 2]`` (so two raters
    scoring 2 and 4 yield 2, never 3). Empty input -> ``{}``.
    """
    dims: set[str] = set()
    for scores in rater_scores.values():
        dims.update(scores)

    consensus: dict[str, int] = {}
    for dim in dims:
        vals = sorted(
            scores[dim] for scores in rater_scores.values() if dim in scores
        )
        if vals:
            consensus[dim] = vals[(len(vals) - 1) // 2]
    return consensus


def cohens_kappa(
    a: list[int],
    b: list[int],
    weights: str | None = None,
    scale: tuple[int, int] = (1, 5),
) -> float:
    """Cohen's kappa over paired scores.

    ``weights=None`` (default) → **unweighted** (nominal): agreement means an
    exact match, suitable for categorical labels.

    ``weights="quadratic"`` → **quadratic-weighted** (ordinal): a near-miss gets
    partial credit, with disagreement weighted ``(i-j)^2 / (smax-smin)^2`` over
    the fixed ``scale`` bounds (default 1–5 → denominator ``16``). This is the
    standard statistic for ordinal/Likert rater agreement and is the one to use
    for the 1–5 QA dimension scores: unweighted kappa collapses toward 0 here
    because the scores cluster (high chance agreement) and a 4-vs-5 "miss" is
    treated the same as 1-vs-5, which contradicts a within-±1 reading.

    ``kappa = (p_o - p_e) / (1 - p_e)`` with the appropriate (weighted) ``p_o`` /
    ``p_e``. Raises ``ValueError`` on length mismatch; empty paired input → 0.0;
    a degenerate ``p_e == 1.0`` → 1.0 iff observers also agree perfectly else 0.0.
    """
    if len(a) != len(b):
        raise ValueError(f"length mismatch: len(a)={len(a)} != len(b)={len(b)}")
    n = len(a)
    if n == 0:
        return 0.0

    categories = set(a) | set(b)
    count_a = {c: a.count(c) for c in categories}
    count_b = {c: b.count(c) for c in categories}

    if weights == "quadratic":
        smin, smax = scale
        span = smax - smin
        if span == 0:  # degenerate single-value scale
            return 1.0
        d2 = span * span

        def agree(i: int, j: int) -> float:
            return 1.0 - ((i - j) ** 2) / d2

        p_o = sum(agree(x, y) for x, y in zip(a, b)) / n
        p_e = sum(
            (count_a[ci] / n) * (count_b[cj] / n) * agree(ci, cj)
            for ci in categories
            for cj in categories
        )
    elif weights is None:
        p_o = sum(1 for x, y in zip(a, b) if x == y) / n
        p_e = sum((count_a[c] / n) * (count_b[c] / n) for c in categories)
    else:
        raise ValueError(f"unknown weights={weights!r}; use None or 'quadratic'")

    if p_e == 1.0:
        return 1.0 if p_o == 1.0 else 0.0
    return (p_o - p_e) / (1 - p_e)


def inter_rater_agreement(
    rater_scores: dict[str, dict[str, int]], tol: int = 1
) -> dict:
    """Human inter-rater baseline over all unordered rater pairs.

    For every pair of raters and every dimension the two share:
      - ``within_tol``: fraction of (pair, dimension) comparisons where
        ``abs(s1 - s2) <= tol`` (mean over all such comparisons).
      - ``mean_pairwise_kappa``: mean of ``cohens_kappa`` across pairs, where
        each pair's two score vectors are built from the shared dimensions in a
        FIXED sorted-dimension order so both vectors align.

    With ``< 2`` raters there are no pairs: returns zeros (does NOT raise).
    """
    raters = list(rater_scores)
    n_raters = len(raters)
    if n_raters < 2:
        return {
            "within_tol": 0.0,
            "mean_pairwise_kappa": 0.0,
            "n_pairs": 0,
            "n_raters": n_raters,
        }

    within_hits = 0
    within_total = 0
    kappas: list[float] = []
    n_pairs = 0

    for r1, r2 in combinations(raters, 2):
        n_pairs += 1
        s1, s2 = rater_scores[r1], rater_scores[r2]
        shared = sorted(set(s1) & set(s2))
        for dim in shared:
            within_total += 1
            if abs(s1[dim] - s2[dim]) <= tol:
                within_hits += 1
        # Only score kappa for pairs that actually share dimensions — a pair
        # with zero overlap contributes no data and must not pull the mean
        # toward 0.0 (cohens_kappa([], []) would return 0.0).
        if shared:
            vec1 = [s1[dim] for dim in shared]
            vec2 = [s2[dim] for dim in shared]
            # Ordinal 1–5 scores → quadratic-weighted kappa (near-misses get
            # partial credit), coherent with the within-±1 reading.
            kappas.append(cohens_kappa(vec1, vec2, weights="quadratic"))

    within_tol = within_hits / within_total if within_total else 0.0
    mean_pairwise_kappa = sum(kappas) / len(kappas) if kappas else 0.0
    return {
        "within_tol": within_tol,
        "mean_pairwise_kappa": mean_pairwise_kappa,
        "n_pairs": n_pairs,
        "n_raters": n_raters,
    }


def aggregate_precision_recall(per_curriculum: list[dict]) -> dict:
    """Aggregate per-curriculum ``precision_recall`` dicts across curricula.

    tp/fp/fn are summed and precision/recall are recomputed from those sums —
    an EXACT micro-average (every gap weighted equally regardless of which
    curriculum it came from). All divisions are guarded.

    ``recall_by_signal_strength`` is the **unweighted mean** of each strength's
    recall across the curricula that have that strength bucket. This is NOT an
    exact micro-average: ``precision_recall`` exposes only per-strength recall
    *fractions*, not the underlying (hits, total) counts, so the per-strength
    bucket sizes are unrecoverable here and cannot be count-weighted exactly.
    The ``note`` key documents this; the tp/fp/fn micro-average is the exact,
    headline number. Empty input -> all zeros and ``{}``.
    """
    tp = sum(c.get("tp", 0) for c in per_curriculum)
    fp = sum(c.get("fp", 0) for c in per_curriculum)
    fn = sum(c.get("fn", 0) for c in per_curriculum)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    strength_vals: dict[str, list[float]] = {}
    for c in per_curriculum:
        for strength, rec in c.get("recall_by_signal_strength", {}).items():
            strength_vals.setdefault(strength, []).append(rec)
    recall_by_signal_strength = {
        strength: (sum(vals) / len(vals) if vals else 0.0)
        for strength, vals in strength_vals.items()
    }

    return {
        "precision": precision,
        "recall": recall,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "recall_by_signal_strength": recall_by_signal_strength,
        "note": (
            "precision/recall are an exact micro-average over summed tp/fp/fn; "
            "recall_by_signal_strength is the unweighted mean of each strength's "
            "per-curriculum recall (bucket sizes are not exposed by "
            "precision_recall, so an exact count-weighted average is not "
            "recoverable here)."
        ),
    }
