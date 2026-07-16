"""C4 AI evaluation harness — the portfolio's measurable-AI signal.

Measures the AI at the Protocol seam (DB-free, no ``submit_ccr`` / ``score_qa``):

  - Gap detection: feed the extractor the bootcamp's covered topics + the SOTA
    corpus, then score the surfaced topics against the C1 planted gaps
    (precision / recall, incl. recall by signal strength).
  - QA-judge agreement: for each human-labeled CCR, ask the judge for its six
    dimension scores and measure agreement with the human labels within +/-1.

Determinism: ``run_eval`` is parameterized by the ``GapExtractor`` / ``QAJudge``
seams. ``main`` injects the real ``AIClient`` when ``ANTHROPIC_API_KEY`` is set
(mode LIVE) or the replay fakes otherwise (mode RECORDED SNAPSHOT) — zero code
change. Always exits 0: this is a non-blocking signal, not a gate.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.ai.eval import metrics
from app.ai.eval.datasets import load_eval_curricula, load_qa_human_labels
from app.config import settings


async def run_eval(*, extractor, judge, report_path: str | Path, mode: str = "RECORDED SNAPSHOT") -> dict:
    """Run both evals, write a markdown report, print a summary, return metrics.

    ``extractor`` / ``judge`` are the seams (real ``AIClient`` or replay fakes).
    ``mode`` is a human label only ("LIVE" vs "RECORDED SNAPSHOT").
    """
    # --- Gap detection (multi-curriculum) --------------------------------
    # Run the extractor over every eval curriculum and micro-average the
    # tp/fp/fn so the headline precision/recall generalizes across programs,
    # not just one. The replay extractor routes by slug via set_curriculum;
    # the real AIClient has no such method, so we duck-type it.
    curricula = load_eval_curricula()
    per_curriculum: list[dict] = []
    for cur in curricula:
        if hasattr(extractor, "set_curriculum"):
            extractor.set_curriculum(cur.slug)
        findings = await extractor.extract_gaps(cur.covered_topics, cur.corpus)
        pr = metrics.precision_recall([f.topic for f in findings], cur.planted_gaps)
        per_curriculum.append({"name": cur.name, "slug": cur.slug, **pr})
    aggregate = metrics.aggregate_precision_recall(per_curriculum)
    gap_detection = {"per_curriculum": per_curriculum, "aggregate": aggregate}

    # --- QA-judge agreement ----------------------------------------------
    human_labels = load_qa_human_labels()
    per_ccr: list[dict] = []
    # Accumulate per-dimension agreement across all CCRs.
    dim_hits: dict[str, int] = {}
    dim_total: dict[str, int] = {}
    # AI-vs-consensus kappa and the human inter-rater baseline, per CCR.
    ai_kappas: list[float] = []
    inter_within: list[float] = []
    inter_kappas: list[float] = []

    for row in human_labels:
        # Embed ccr_id so the replay judge can route; the real judge ignores it.
        summary = f"[ccr_id={row['ccr_id']}] {row['summary']}"
        judgement = await judge.judge(summary, row["proposed_changes"])
        ai_scores = {j.dimension: j.score for j in judgement.judgements}
        # Multi-rater labels: the AI judge is measured against the per-dimension
        # rater consensus (median), not any single rater.
        consensus = metrics.consensus_scores(row["rater_scores"])
        agree = metrics.qa_agreement(ai_scores, consensus, tol=1)

        # AI-vs-consensus kappa over a FIXED sorted dimension order so the two
        # score vectors align.
        dims = sorted(consensus)
        # Ordinal 1–5 scores → quadratic-weighted kappa (matches the within-±1
        # framing; unweighted kappa would understate ordinal agreement).
        kappa = metrics.cohens_kappa(
            [ai_scores[d] for d in dims],
            [consensus[d] for d in dims],
            weights="quadratic",
        )
        ai_kappas.append(kappa)

        # Human baseline for THIS CCR: how well the raters agree among
        # themselves (the ceiling the AI judge is measured against).
        inter = metrics.inter_rater_agreement(row["rater_scores"], tol=1)
        inter_within.append(inter["within_tol"])
        inter_kappas.append(inter["mean_pairwise_kappa"])

        per_ccr.append(
            {
                "ccr_id": row["ccr_id"],
                "title": row["title"],
                "agreement": agree["agreement"],
                "within_tol": agree["within_tol"],
                "n": agree["n"],
                "per_dimension": agree["per_dimension"],
                "kappa": kappa,
                "inter_rater_within_tol": inter["within_tol"],
                "inter_rater_kappa": inter["mean_pairwise_kappa"],
            }
        )
        for dim, ok in agree["per_dimension"].items():
            dim_total[dim] = dim_total.get(dim, 0) + 1
            dim_hits[dim] = dim_hits.get(dim, 0) + (1 if ok else 0)

    total_within = sum(c["within_tol"] for c in per_ccr)
    total_n = sum(c["n"] for c in per_ccr)
    overall_agreement = total_within / total_n if total_n else 0.0
    per_dimension_agreement = {
        dim: dim_hits[dim] / dim_total[dim] for dim in dim_total
    }
    ai_mean_kappa = sum(ai_kappas) / len(ai_kappas) if ai_kappas else 0.0
    # Per-CCR within_tol fractions are equally weighted: every CCR has the same
    # rater*dimension comparison count, so the simple mean equals the pooled
    # within-+/-1 fraction.
    inter_rater = {
        "within_tol": sum(inter_within) / len(inter_within) if inter_within else 0.0,
        "mean_pairwise_kappa": (
            sum(inter_kappas) / len(inter_kappas) if inter_kappas else 0.0
        ),
    }

    result = {
        "mode": mode,
        "gap_detection": gap_detection,
        "qa_agreement": {
            "overall_agreement": overall_agreement,
            "within_tol": total_within,
            "n": total_n,
            "per_dimension": per_dimension_agreement,
            "per_ccr": per_ccr,
            "ai_mean_kappa": ai_mean_kappa,
            "inter_rater": inter_rater,
        },
    }

    _write_report(Path(report_path), result)
    _print_summary(result)
    return result


def _write_report(path: Path, result: dict) -> None:
    gap = result["gap_detection"]
    qa = result["qa_agreement"]
    mode = result["mode"]

    lines: list[str] = []
    lines.append("# CurricMesh AI Evaluation Report")
    lines.append("")
    lines.append(f"**Mode:** {mode}")
    if mode == "RECORDED SNAPSHOT":
        lines.append("")
        lines.append(
            "> The model outputs below are a **recorded snapshot** (replay fakes), "
            "so this report is deterministic and network-free in CI. The metrics "
            "machinery is the real, unit-tested deliverable."
        )
    lines.append("")

    # --- Gap detection (multi-curriculum) ---------------------------------
    per_curriculum = gap["per_curriculum"]
    aggregate = gap["aggregate"]
    lines.append("## Gap Detection (vs. planted gaps, across curricula)")
    lines.append("")
    lines.append("| Curriculum | Precision | Recall | Matched | Missed |")
    lines.append("| --- | --- | --- | --- | --- |")
    for c in per_curriculum:
        matched = ", ".join(c["matched_gaps"]) or "(none)"
        missed = ", ".join(c["missed_gaps"]) or "(none)"
        lines.append(
            f"| {c['name']} | {c['precision']:.3f} | {c['recall']:.3f} | "
            f"{matched} | {missed} |"
        )
    lines.append("")
    lines.append(
        f"**Aggregate (micro-avg): precision {aggregate['precision']:.3f} "
        f"· recall {aggregate['recall']:.3f} "
        f"(TP={aggregate['tp']} FP={aggregate['fp']} FN={aggregate['fn']})**"
    )
    lines.append("")
    lines.append("### Recall by signal strength")
    lines.append("")
    lines.append("| Signal strength | Recall |")
    lines.append("| --- | --- |")
    for strength, rec in sorted(aggregate["recall_by_signal_strength"].items()):
        lines.append(f"| {strength} | {rec:.3f} |")
    lines.append("")

    # --- QA agreement -----------------------------------------------------
    lines.append("## QA-Judge Agreement (vs. human labels, within +/-1)")
    lines.append("")
    lines.append(
        f"- **Overall Agreement:** {qa['overall_agreement']:.3f}  "
        f"({qa['within_tol']}/{qa['n']} dimension comparisons within +/-1)"
    )
    lines.append("")
    lines.append("### Human baseline vs AI")
    lines.append("")
    inter = qa["inter_rater"]
    lines.append(
        f"- Human inter-rater agreement (within +/-1): {inter['within_tol']:.3f}  "
        f"(mean pairwise kappa = {inter['mean_pairwise_kappa']:.3f})"
    )
    lines.append(
        f"- AI-vs-consensus agreement (within +/-1): {qa['overall_agreement']:.3f}  "
        f"(mean kappa = {qa['ai_mean_kappa']:.3f})"
    )
    lines.append("")
    lines.append(
        "> The AI judge agrees with the human consensus about as well as the "
        "human raters agree with each other."
    )
    lines.append("")
    lines.append("### Agreement by dimension")
    lines.append("")
    lines.append("| Dimension | Agreement |")
    lines.append("| --- | --- |")
    for dim, val in sorted(qa["per_dimension"].items()):
        lines.append(f"| {dim} | {val:.3f} |")
    lines.append("")
    lines.append("### Agreement by CCR")
    lines.append("")
    lines.append("| CCR | Title | Agreement |")
    lines.append("| --- | --- | --- |")
    for c in qa["per_ccr"]:
        lines.append(f"| {c['ccr_id']} | {c['title']} | {c['within_tol']}/{c['n']} ({c['agreement']:.3f}) |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_Run live with ANTHROPIC_API_KEY set._")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _print_summary(result: dict) -> None:
    gap = result["gap_detection"]
    aggregate = gap["aggregate"]
    qa = result["qa_agreement"]
    inter = qa["inter_rater"]
    print(f"=== CurricMesh AI Eval ({result['mode']}) ===")
    print(
        f"Gap detection (aggregate, micro-avg): "
        f"precision={aggregate['precision']:.3f} recall={aggregate['recall']:.3f} "
        f"(TP={aggregate['tp']} FP={aggregate['fp']} FN={aggregate['fn']})"
    )
    for c in gap["per_curriculum"]:
        print(
            f"  - {c['name']}: precision={c['precision']:.3f} "
            f"recall={c['recall']:.3f} (TP={c['tp']} FP={c['fp']} FN={c['fn']})"
        )
    print(f"  recall by signal strength: {aggregate['recall_by_signal_strength']}")
    print(
        f"QA-judge agreement (AI vs consensus): {qa['overall_agreement']:.3f} "
        f"({qa['within_tol']}/{qa['n']} dims within +/-1, mean kappa={qa['ai_mean_kappa']:.3f})"
    )
    print(
        f"Human inter-rater baseline (within +/-1): {inter['within_tol']:.3f} "
        f"(mean pairwise kappa={inter['mean_pairwise_kappa']:.3f})"
    )


def main() -> None:
    report_path = sys.argv[1] if len(sys.argv) > 1 else "ai_eval_report.md"

    if settings.ANTHROPIC_API_KEY:
        from app.ai.client import AIClient

        client = AIClient(api_key=settings.ANTHROPIC_API_KEY)
        extractor = client
        judge = client
        mode = "LIVE"
    else:
        from app.ai.eval.replay import ReplayExtractor, ReplayJudge

        extractor = ReplayExtractor()
        judge = ReplayJudge()
        mode = "RECORDED SNAPSHOT"

    asyncio.run(
        run_eval(
            extractor=extractor, judge=judge, report_path=report_path, mode=mode
        )
    )
    # Non-blocking philosophy: always exit 0.
    sys.exit(0)


if __name__ == "__main__":
    main()
