"""Tests for the C4 AI evaluation harness.

The eval is DB-free and operates at the Protocol seam: it calls
``extractor.extract_gaps`` and ``judge.judge`` directly. These tests inject the
deterministic replay fakes — ZERO real Anthropic calls / network.

Metrics are PURE functions, exercised here with crafted inputs so the expected
precision/recall/agreement are computable by hand.
"""

from __future__ import annotations

import pytest

from app.ai.eval import datasets, metrics
from app.ai.eval.replay import ReplayExtractor, ReplayJudge
from app.ai.eval.run_eval import run_eval
from app.ai.schemas import CorpusDoc
from app.core.workflow.rules import QA_DIMENSIONS


# ---------------------------------------------------------------------------
# build_match_terms / topic_hits
# ---------------------------------------------------------------------------


def test_build_match_terms_extracts_parenthetical_abbreviation():
    gap = {"topic": "Model Context Protocol (MCP)", "canonical_tags": ["Model Context Protocol (MCP)"]}
    terms = metrics.build_match_terms(gap)
    assert "model context protocol (mcp)" in terms
    assert "mcp" in terms  # extracted from the parenthetical


def test_topic_hits_mcp_abbreviation():
    gaps = datasets.load_planted_gaps()
    mcp_gap = next(g for g in gaps if g["topic"].startswith("Model Context"))
    terms = metrics.build_match_terms(mcp_gap)
    assert metrics.topic_hits("we standardized on MCP across all teams", terms)


def test_topic_hits_finetuning_tags():
    gaps = datasets.load_planted_gaps()
    ft_gap = next(g for g in gaps if "Fine-tuning" in g["topic"])
    terms = metrics.build_match_terms(ft_gap)
    assert metrics.topic_hits("DPO and LoRA tuning of agent traces", terms)


def test_topic_hits_unrelated_string_misses():
    gaps = datasets.load_planted_gaps()
    mcp_gap = next(g for g in gaps if g["topic"].startswith("Model Context"))
    terms = metrics.build_match_terms(mcp_gap)
    assert not metrics.topic_hits("kubernetes deployment of agents", terms)


def test_short_abbreviation_uses_word_boundary():
    # 'SFT' must not spuriously match inside 'shifting' / 'crafting'.
    gap = {"topic": "Supervised Fine-Tuning (SFT)", "canonical_tags": ["SFT"]}
    terms = metrics.build_match_terms(gap)
    assert not metrics.topic_hits("we are crafting the curriculum", terms)
    assert metrics.topic_hits("the SFT stage uses agent traces", terms)


# ---------------------------------------------------------------------------
# precision_recall
# ---------------------------------------------------------------------------


def test_precision_recall_crafted_2tp_1fp_1fn():
    planted = [
        {"topic": "Model Context Protocol (MCP)", "canonical_tags": ["Model Context Protocol (MCP)"], "signal_strength": "critical"},
        {"topic": "Agent Observability", "canonical_tags": ["observability", "OpenTelemetry"], "signal_strength": "critical"},
        {"topic": "Fine-tuning", "canonical_tags": ["fine-tuning", "DPO", "LoRA"], "signal_strength": "moderate"},
    ]
    found = [
        "Model Context Protocol (MCP)",   # TP -> gap 1
        "Agent Observability with OpenTelemetry",  # TP -> gap 2
        "Kubernetes for Agents",          # FP -> no gap
    ]
    res = metrics.precision_recall(found, planted)
    assert res["tp"] == 2
    assert res["fp"] == 1
    assert res["fn"] == 1
    assert res["precision"] == pytest.approx(2 / 3)
    assert res["recall"] == pytest.approx(2 / 3)
    assert "Kubernetes for Agents" in res["false_positives"]
    assert "Fine-tuning" in res["missed_gaps"]
    # recall-by-signal-strength: criticals both found (2/2), moderate missed (0/1)
    assert res["recall_by_signal_strength"]["critical"] == pytest.approx(1.0)
    assert res["recall_by_signal_strength"]["moderate"] == pytest.approx(0.0)


def test_precision_recall_empty_inputs_no_zero_division():
    res = metrics.precision_recall([], [])
    assert res["precision"] == 0.0
    assert res["recall"] == 0.0
    assert res["tp"] == 0 and res["fp"] == 0 and res["fn"] == 0


def test_precision_recall_each_gap_counts_once():
    # Two found topics both hitting the SAME gap -> 1 TP, not 2.
    planted = [
        {"topic": "Model Context Protocol (MCP)", "canonical_tags": ["Model Context Protocol (MCP)"], "signal_strength": "critical"},
    ]
    found = ["MCP server development", "the MCP specification"]
    res = metrics.precision_recall(found, planted)
    assert res["tp"] == 1
    assert res["fn"] == 0
    # both found topics hit a gap, so neither is a false positive
    assert res["fp"] == 0
    assert res["precision"] == pytest.approx(1.0)
    assert res["recall"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# qa_agreement
# ---------------------------------------------------------------------------


def test_qa_agreement_exact_fraction():
    human = {"a": 5, "b": 4, "c": 3, "d": 2}
    ai = {"a": 5, "b": 5, "c": 1, "d": 2}  # a ok, b ok(+1), c off(2), d ok
    res = metrics.qa_agreement(ai, human, tol=1)
    assert res["n"] == 4
    assert res["within_tol"] == 3
    assert res["agreement"] == pytest.approx(3 / 4)
    assert res["per_dimension"] == {"a": True, "b": True, "c": False, "d": True}


def test_qa_agreement_empty_no_zero_division():
    res = metrics.qa_agreement({}, {}, tol=1)
    assert res["agreement"] == 0.0
    assert res["n"] == 0


def test_qa_agreement_raises_on_missing_ai_dimension():
    # A human-scored dimension absent from ai_scores must NOT be silently
    # dropped (which would inflate agreement over a smaller denominator).
    human = {"a": 5, "b": 4}
    ai = {"a": 5}  # missing "b"
    with pytest.raises(ValueError, match="missing dimensions"):
        metrics.qa_agreement(ai, human, tol=1)
    # But empty/empty is the no-op base case, not a contract violation.
    res = metrics.qa_agreement({}, {}, tol=1)
    assert res["agreement"] == 0.0
    assert res["n"] == 0


# ---------------------------------------------------------------------------
# consensus_scores
# ---------------------------------------------------------------------------


def test_consensus_scores_per_dimension_median():
    # 3 raters. Dim A: {2, 3, 5} -> median 3. Dim B: {5, 3, 5} -> median 5.
    # Dim C: {5, 4, 5} -> median 5.
    rater_scores = {
        "r1": {"A": 2, "B": 5, "C": 5},
        "r2": {"A": 3, "B": 3, "C": 4},
        "r3": {"A": 5, "B": 5, "C": 5},
    }
    assert metrics.consensus_scores(rater_scores) == {"A": 3, "B": 5, "C": 5}


def test_consensus_scores_even_count_takes_lower_middle():
    # Dim A: {2, 4} sorted -> [2, 4], (len-1)//2 = 0 -> 2 (lower-middle).
    rater_scores = {"r1": {"A": 2}, "r2": {"A": 4}}
    assert metrics.consensus_scores(rater_scores) == {"A": 2}


def test_consensus_scores_union_of_dimensions():
    # Dimensions are the union; each median uses only raters that have the dim.
    rater_scores = {
        "r1": {"A": 1},
        "r2": {"A": 3, "B": 7},
    }
    # A: {1, 3} -> lower-middle 1. B: {7} -> 7.
    assert metrics.consensus_scores(rater_scores) == {"A": 1, "B": 7}


def test_consensus_scores_empty():
    assert metrics.consensus_scores({}) == {}


# ---------------------------------------------------------------------------
# cohens_kappa
# ---------------------------------------------------------------------------


def test_cohens_kappa_perfect_agreement():
    assert metrics.cohens_kappa([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == pytest.approx(1.0)


def test_cohens_kappa_total_disagreement_le_zero():
    # No exact agreement on any pair -> p_o = 0 -> kappa <= 0.
    a = [1, 1, 2, 2]
    b = [2, 2, 1, 1]
    assert metrics.cohens_kappa(a, b) <= 0.0


def test_cohens_kappa_length_mismatch_raises():
    with pytest.raises(ValueError):
        metrics.cohens_kappa([1, 2, 3], [1, 2])


def test_cohens_kappa_empty():
    assert metrics.cohens_kappa([], []) == 0.0


def test_cohens_kappa_degenerate_pe_one():
    # Both raters always score the same single category -> p_e == 1.0.
    # Perfect agreement -> 1.0.
    assert metrics.cohens_kappa([3, 3, 3], [3, 3, 3]) == pytest.approx(1.0)


def test_cohens_kappa_quadratic_perfect_agreement():
    assert (
        metrics.cohens_kappa([1, 2, 3, 4, 5], [1, 2, 3, 4, 5], weights="quadratic")
        == pytest.approx(1.0)
    )


def test_cohens_kappa_quadratic_credits_near_miss_over_unweighted():
    # Ordinal scores that are mostly within ±1 but rarely exactly equal:
    # quadratic-weighted kappa must exceed the unweighted (exact-match) kappa,
    # which collapses toward 0 because near-misses get no credit.
    a = [5, 4, 5, 4, 3, 4]
    b = [5, 5, 4, 4, 4, 3]  # several 1-step disagreements, no big gaps
    k_unweighted = metrics.cohens_kappa(a, b)
    k_quadratic = metrics.cohens_kappa(a, b, weights="quadratic")
    assert k_quadratic > k_unweighted
    assert k_quadratic > 0.0  # ordinal agreement is meaningfully positive


def test_cohens_kappa_quadratic_one_step_vs_far_miss():
    # Same rater-a vector and the SAME marginal distribution for b in both cases
    # (both b's are permutations of {1..5}), so p_e is identical — only the
    # *magnitude* of disagreement differs. 1-step swaps must score higher than
    # 4-step swaps under quadratic weighting.
    a = [1, 2, 3, 4, 5]
    b_near = [1, 2, 4, 3, 5]  # 3<->4 swapped: two 1-step misses
    b_far = [5, 2, 3, 4, 1]  # 1<->5 swapped: two 4-step misses
    near = metrics.cohens_kappa(a, b_near, weights="quadratic", scale=(1, 5))
    far = metrics.cohens_kappa(a, b_far, weights="quadratic", scale=(1, 5))
    assert near > far


def test_cohens_kappa_unknown_weights_raises():
    with pytest.raises(ValueError, match="unknown weights"):
        metrics.cohens_kappa([1, 2], [1, 2], weights="linear")


# ---------------------------------------------------------------------------
# inter_rater_agreement
# ---------------------------------------------------------------------------


def test_inter_rater_agreement_known_within_tol_fraction():
    # 3 raters, dims A,B,C shared by all. 3 unordered pairs -> 9 comparisons.
    rater_scores = {
        "r1": {"A": 5, "B": 5, "C": 5},
        "r2": {"A": 5, "B": 4, "C": 1},
        "r3": {"A": 3, "B": 5, "C": 5},
    }
    # Pair (r1,r2): A |5-5|=0 ok, B |5-4|=1 ok, C |5-1|=4 NO  -> 2/3
    # Pair (r1,r3): A |5-3|=2 NO, B |5-5|=0 ok, C |5-5|=0 ok  -> 2/3
    # Pair (r2,r3): A |5-3|=2 NO, B |4-5|=1 ok, C |1-5|=4 NO  -> 1/3
    # within_tol = (2+2+1)/9 = 5/9
    res = metrics.inter_rater_agreement(rater_scores, tol=1)
    assert res["within_tol"] == pytest.approx(5 / 9)
    assert res["n_pairs"] == 3
    assert res["n_raters"] == 3
    assert -1.0 <= res["mean_pairwise_kappa"] <= 1.0


def test_inter_rater_agreement_single_rater_no_raise():
    res = metrics.inter_rater_agreement({"r1": {"A": 5}}, tol=1)
    assert res["n_pairs"] == 0
    assert res["n_raters"] == 1
    assert res["within_tol"] == 0.0
    assert res["mean_pairwise_kappa"] == 0.0


def test_inter_rater_agreement_empty_no_raise():
    res = metrics.inter_rater_agreement({}, tol=1)
    assert res["n_pairs"] == 0
    assert res["n_raters"] == 0


def test_inter_rater_agreement_dimension_order_independent():
    # Same scores, reversed dict-insertion order: the FIXED sorted-dimension
    # alignment must still pair A-with-A and B-with-B → perfect agreement.
    # If the sorted() alignment were dropped, vec1=[5,3] vs vec2=[3,5] would
    # look like total disagreement and kappa would not be 1.0.
    res = metrics.inter_rater_agreement(
        {"r1": {"B": 5, "A": 3}, "r2": {"A": 3, "B": 5}}, tol=1
    )
    assert res["n_pairs"] == 1
    assert res["within_tol"] == pytest.approx(1.0)
    assert res["mean_pairwise_kappa"] == pytest.approx(1.0)


def test_inter_rater_agreement_no_shared_dimensions():
    # A pair sharing zero dimensions contributes no data: it must NOT append a
    # spurious 0.0 kappa that drags mean_pairwise_kappa down.
    res = metrics.inter_rater_agreement(
        {"r1": {"A": 5}, "r2": {"B": 5}}, tol=1
    )
    assert res["n_pairs"] == 1
    assert res["within_tol"] == 0.0  # no shared comparisons
    assert res["mean_pairwise_kappa"] == 0.0  # kappas list empty, guarded


# ---------------------------------------------------------------------------
# aggregate_precision_recall
# ---------------------------------------------------------------------------


def test_aggregate_precision_recall_micro_average():
    c1 = {
        "precision": 2 / 3, "recall": 2 / 3, "tp": 2, "fp": 1, "fn": 1,
        "recall_by_signal_strength": {"critical": 1.0, "moderate": 0.0},
    }
    c2 = {
        "precision": 3 / 4, "recall": 3 / 5, "tp": 3, "fp": 1, "fn": 2,
        "recall_by_signal_strength": {"critical": 0.5, "weak": 1.0},
    }
    res = metrics.aggregate_precision_recall([c1, c2])
    assert res["tp"] == 5
    assert res["fp"] == 2
    assert res["fn"] == 3
    # micro-average: precision = tp/(tp+fp) = 5/7, recall = tp/(tp+fn) = 5/8.
    assert res["precision"] == pytest.approx(5 / 7)
    assert res["recall"] == pytest.approx(5 / 8)
    # critical present in both -> mean(1.0, 0.5) = 0.75; moderate only c1 -> 0.0;
    # weak only c2 -> 1.0
    assert res["recall_by_signal_strength"]["critical"] == pytest.approx(0.75)
    assert res["recall_by_signal_strength"]["moderate"] == pytest.approx(0.0)
    assert res["recall_by_signal_strength"]["weak"] == pytest.approx(1.0)
    assert "note" in res


def test_aggregate_precision_recall_empty_no_zero_division():
    res = metrics.aggregate_precision_recall([])
    assert res["precision"] == 0.0
    assert res["recall"] == 0.0
    assert res["tp"] == 0 and res["fp"] == 0 and res["fn"] == 0
    assert res["recall_by_signal_strength"] == {}


# ---------------------------------------------------------------------------
# datasets
# ---------------------------------------------------------------------------


def test_load_planted_gaps_returns_three():
    gaps = datasets.load_planted_gaps()
    assert len(gaps) == 3
    assert all("canonical_tags" in g and "signal_strength" in g for g in gaps)


def test_load_corpus_returns_forty_docs():
    docs = datasets.load_corpus()
    assert len(docs) == 40
    assert all(d.title and d.body for d in docs)


def test_load_qa_human_labels_multi_rater_all_dims():
    rows = datasets.load_qa_human_labels()
    assert len(rows) == 4
    expected = set(QA_DIMENSIONS)
    for row in rows:
        raters = row["rater_scores"]
        assert len(raters) >= 3
        for scores in raters.values():
            assert set(scores.keys()) == expected


def test_validate_rater_scores_missing_dimension_raises():
    # Drop one dimension from an otherwise-valid rater.
    bad = {d: 4 for d in QA_DIMENSIONS if d != "consistency"}
    with pytest.raises(ValueError) as exc:
        datasets._validate_rater_scores("ccr-eval-001", "rater_B", bad)
    msg = str(exc.value)
    assert "rater_B" in msg
    assert "ccr-eval-001" in msg
    assert "consistency" in msg  # named as a missing dimension


def test_validate_rater_scores_unexpected_dimension_raises():
    bad = {d: 4 for d in QA_DIMENSIONS}
    bad["bogus_dim"] = 3
    with pytest.raises(ValueError) as exc:
        datasets._validate_rater_scores("ccr-eval-002", "rater_C", bad)
    msg = str(exc.value)
    assert "rater_C" in msg
    assert "bogus_dim" in msg


def test_load_qa_human_labels_too_few_raters_raises(monkeypatch):
    # One-rater row must fail loudly so an inter-rater baseline is computable.
    single = {
        "labels": [
            {
                "ccr_id": "ccr-eval-999",
                "rater_scores": {"rater_A": {d: 4 for d in QA_DIMENSIONS}},
            }
        ]
    }
    monkeypatch.setattr(datasets, "_load_json", lambda path: single)
    with pytest.raises(ValueError) as exc:
        datasets.load_qa_human_labels()
    assert "ccr-eval-999" in str(exc.value)


def test_load_qa_human_labels_inter_rater_within_realistic_band():
    # Feeding each loaded row's rater_scores to inter_rater_agreement yields a
    # realistic (non-trivial, non-chaotic) within-+/-1 fraction.
    rows = datasets.load_qa_human_labels()
    for row in rows:
        res = metrics.inter_rater_agreement(row["rater_scores"], tol=1)
        assert 0.70 <= res["within_tol"] <= 1.0

    # Pooled across every (pair, dimension) comparison in all rows.
    hits = total = 0
    for row in rows:
        rs = row["rater_scores"]
        raters = list(rs)
        for i in range(len(raters)):
            for j in range(i + 1, len(raters)):
                a, b = rs[raters[i]], rs[raters[j]]
                for d in set(a) & set(b):
                    total += 1
                    if abs(a[d] - b[d]) <= 1:
                        hits += 1
    assert 0.80 <= hits / total <= 0.95


def test_bootcamp_covered_topics_excludes_planted_gaps():
    covered = " ".join(datasets.bootcamp_covered_topics()).lower()
    assert "mcp" not in covered
    assert "observability" not in covered
    assert "fine-tun" not in covered


# ---------------------------------------------------------------------------
# load_eval_curricula (RE3: multi-curriculum)
# ---------------------------------------------------------------------------


def test_load_eval_curricula_returns_at_least_two_well_formed():
    curricula = datasets.load_eval_curricula()
    assert len(curricula) >= 2
    slugs = [c.slug for c in curricula]
    assert len(slugs) == len(set(slugs))  # unique slugs
    for c in curricula:
        assert isinstance(c, datasets.EvalCurriculum)
        assert c.name and c.slug
        assert c.covered_topics  # non-empty
        assert c.corpus and all(isinstance(d, CorpusDoc) for d in c.corpus)
        assert c.planted_gaps and all(
            "canonical_tags" in g and "signal_strength" in g
            for g in c.planted_gaps
        )


def test_curriculum_1_covered_topics_have_no_eval_routing_marker():
    # A LIVE run must not see eval-routing junk: curriculum 1 reuses the existing
    # CLEAN loader, so its covered surface equals bootcamp_covered_topics().
    curricula = datasets.load_eval_curricula()
    c1 = next(c for c in curricula if c.slug == "agentic-ai-production")
    assert c1.covered_topics == datasets.bootcamp_covered_topics()


def test_curriculum_2_covered_topics_genuinely_exclude_planted_gaps():
    curricula = datasets.load_eval_curricula()
    c2 = next(c for c in curricula if c.slug == "cloud-platform-engineering")
    # None of the planted-gap topic strings may appear in the covered list, so
    # the gaps are genuinely uncovered when measuring recall.
    for gap in c2.planted_gaps:
        assert gap["topic"] not in c2.covered_topics


# ---------------------------------------------------------------------------
# curriculum-aware ReplayExtractor (RE3)
# ---------------------------------------------------------------------------


def _cur(slug: str):
    return next(
        c for c in datasets.load_eval_curricula() if c.slug == slug
    )


@pytest.mark.asyncio
async def test_replay_extractor_default_is_curriculum_1():
    # A bare ReplayExtractor() (no set_curriculum) returns curriculum-1 findings,
    # preserving the documented 2/3 . 2/3 result and the existing e2e path.
    c1 = _cur("agentic-ai-production")
    ex = ReplayExtractor()
    findings = await ex.extract_gaps(c1.covered_topics, c1.corpus)
    res = metrics.precision_recall([f.topic for f in findings], c1.planted_gaps)
    assert res["precision"] == pytest.approx(2 / 3)
    assert res["recall"] == pytest.approx(2 / 3)
    assert res["tp"] == 2 and res["fp"] == 1 and res["fn"] == 1


@pytest.mark.asyncio
async def test_replay_extractor_set_curriculum_routes_to_curriculum_2():
    c1 = _cur("agentic-ai-production")
    c2 = _cur("cloud-platform-engineering")
    ex = ReplayExtractor()

    findings_1 = await ex.extract_gaps(c1.covered_topics, c1.corpus)
    ex.set_curriculum("cloud-platform-engineering")
    findings_2 = await ex.extract_gaps(c2.covered_topics, c2.corpus)

    topics_1 = {f.topic for f in findings_1}
    topics_2 = {f.topic for f in findings_2}
    assert topics_1 != topics_2  # genuinely different findings
    # curriculum-2 findings talk about eBPF / IDP, not MCP.
    joined_2 = " ".join(topics_2).lower()
    assert "ebpf" in joined_2
    assert "mcp" not in joined_2


@pytest.mark.asyncio
async def test_replay_extractor_curriculum_2_precision_recall():
    # Documented intended numbers for curriculum 2: detect all 3 gaps (eBPF,
    # IDP, FinOps) + 1 false positive -> precision 3/4, recall 1.0. Distinct
    # from curriculum 1 (2/3 . 2/3) so the aggregate is a real micro-average.
    c2 = _cur("cloud-platform-engineering")
    ex = ReplayExtractor()
    ex.set_curriculum("cloud-platform-engineering")
    findings = await ex.extract_gaps(c2.covered_topics, c2.corpus)
    res = metrics.precision_recall([f.topic for f in findings], c2.planted_gaps)
    assert res["tp"] == 3 and res["fp"] == 1 and res["fn"] == 0
    assert res["precision"] == pytest.approx(3 / 4)
    assert res["recall"] == pytest.approx(1.0)


def test_eval_curriculum_micro_average_is_real_not_a_copy():
    # The per-curriculum results differ, so the aggregate is a genuine
    # micro-average over summed tp/fp/fn (5/7 precision, 5/6 recall), not a copy
    # of either curriculum.
    c1 = {"tp": 2, "fp": 1, "fn": 1, "recall_by_signal_strength": {}}
    c2 = {"tp": 3, "fp": 1, "fn": 0, "recall_by_signal_strength": {}}
    agg = metrics.aggregate_precision_recall([c1, c2])
    assert agg["tp"] == 5 and agg["fp"] == 2 and agg["fn"] == 1
    assert agg["precision"] == pytest.approx(5 / 7)
    assert agg["recall"] == pytest.approx(5 / 6)


# ---------------------------------------------------------------------------
# end-to-end run_eval with replay fakes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_eval_replay_end_to_end(tmp_path):
    report = tmp_path / "eval_report.md"
    result = await run_eval(
        extractor=ReplayExtractor(),
        judge=ReplayJudge(),
        report_path=report,
    )

    gap = result["gap_detection"]
    # Multi-curriculum: two per-curriculum entries with their documented numbers.
    per_curriculum = gap["per_curriculum"]
    assert len(per_curriculum) == 2
    by_slug = {c["slug"]: c for c in per_curriculum}
    c1 = by_slug["agentic-ai-production"]
    assert c1["tp"] == 2 and c1["fp"] == 1 and c1["fn"] == 1
    assert c1["precision"] == pytest.approx(2 / 3)
    assert c1["recall"] == pytest.approx(2 / 3)
    c2 = by_slug["cloud-platform-engineering"]
    assert c2["tp"] == 3 and c2["fp"] == 1 and c2["fn"] == 0
    assert c2["precision"] == pytest.approx(3 / 4)
    assert c2["recall"] == pytest.approx(1.0)

    # Aggregate is an exact micro-average over summed tp/fp/fn.
    agg = gap["aggregate"]
    assert agg["tp"] == 5 and agg["fp"] == 2 and agg["fn"] == 1
    assert agg["precision"] == pytest.approx(5 / 7)
    assert agg["recall"] == pytest.approx(5 / 6)

    qa = result["qa_agreement"]
    # Deterministic by construction: AI judge vs. the per-dimension rater
    # consensus (median), 21/24 dims within +/-1.
    assert qa["overall_agreement"] == pytest.approx(21 / 24)

    # Human inter-rater baseline (pooled within-+/-1 over all pair*dim comparisons).
    inter = qa["inter_rater"]
    assert inter["within_tol"] == pytest.approx(0.861, abs=0.005)  # ~62/72
    assert isinstance(inter["mean_pairwise_kappa"], float)
    assert -1.0 <= inter["mean_pairwise_kappa"] <= 1.0
    assert isinstance(qa["ai_mean_kappa"], float)

    assert report.exists()
    text = report.read_text()
    assert "Precision" in text
    assert "Recall" in text
    assert "Agreement" in text
    assert "RECORDED SNAPSHOT" in text
    # New richer-report markers.
    assert "Cloud Platform Engineering" in text  # per-curriculum row
    assert "Aggregate" in text
    assert "Human inter-rater agreement" in text
    assert "mean pairwise" in text  # kappa values surfaced
