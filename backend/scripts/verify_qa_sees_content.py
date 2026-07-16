"""Prove the AI QA judge actually SEES generated course content (PRs #75 + #76).

Two tiers, so you can close the verification with or without spending an API call:

  TIER 1 (free, no Anthropic call) — reconstruct the EXACT ``proposed_changes``
    string ``score_qa`` hands the judge for a real CCR, and show that the lesson
    bodies are in it. This alone proves the plumbing: content reaches the judge.

  TIER 2 (--live, one real Anthropic call) — actually run the real ``AIClient``
    judge on that input and print the six dimension scores + EVIDENCE. The
    evidence should quote/paraphrase the real content — the end-to-end proof.

It auto-selects the most recent authored initial-release CCR (``impact ?
'initial_release'``) unless you pass ``CCR_ID``. Read-only in Tier 1; Tier 2
does not persist a QAReview (it calls the judge directly, never ``score_qa``).

Accepts any Render DB URL form (postgres:// / +asyncpg, ssl auto-added), like
the other ops scripts.

Usage (from backend/, with the venv active and the External DATABASE_URL set):
    # free plumbing proof:
    python -m scripts.verify_qa_sees_content
    # real scoring run (spends ~1 Anthropic call; needs ANTHROPIC_API_KEY):
    ANTHROPIC_API_KEY=sk-... python -m scripts.verify_qa_sees_content --live
    # a specific CCR / a different tenant:
    CCR_ID=<uuid> CCR_ORG=<org-uuid> python -m scripts.verify_qa_sees_content
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.ai.qa_judge import (
    _build_proposed_changes,
    _build_summary,
    _load_initial_release_bodies,
)
from app.models.workflow import ChangeRequest

# The org authored/demo content lives under in prod (Career Forge). Overridable
# via CCR_ORG for another tenant — RLS hides rows outside the GUC's org.
DEFAULT_ORG = "da1d5edf-e37e-447e-83b1-a1725a7fff86"


def _normalize(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    if all(h not in url for h in ("localhost", "127.0.0.1")) and "ssl=" not in url and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "ssl=require"
    return url


async def main() -> None:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        print("ERROR: export the Render External DATABASE_URL first.")
        sys.exit(1)
    live = "--live" in sys.argv
    org = os.environ.get("CCR_ORG", DEFAULT_ORG)
    ccr_id = os.environ.get("CCR_ID")

    engine = create_async_engine(_normalize(raw))
    async with AsyncSession(engine) as session:
        # RLS: pin the tenant so the owner role can read the org's rows.
        await session.execute(
            text("SELECT set_config('app.current_org', :org, false)"), {"org": org}
        )

        if not ccr_id:
            row = (
                await session.execute(
                    text(
                        "SELECT id FROM change_requests "
                        "WHERE impact ? 'initial_release' "
                        "ORDER BY created_at DESC LIMIT 1"
                    )
                )
            ).first()
            if not row:
                print(
                    f"No initial-release CCR found in org {org}. "
                    "Pass CCR_ID=<uuid> (and CCR_ORG=<uuid> if a different tenant)."
                )
                sys.exit(2)
            ccr_id = str(row[0])

        ccr = await session.get(ChangeRequest, ccr_id)
        if ccr is None:
            print(f"CCR {ccr_id} not visible in org {org} (RLS). Check CCR_ORG.")
            sys.exit(2)

        # Reconstruct the judge's real input, exactly as score_qa builds it.
        summary = _build_summary(ccr)
        proposed = _build_proposed_changes(ccr)
        initrel = await _load_initial_release_bodies(session, ccr)
        if initrel:
            proposed = f"{proposed}\n\n{initrel}"

        has_bodies = "PROPOSED CONTENT" in proposed
        source = "change_set" if (ccr.change_set and "PROPOSED CONTENT" in _build_proposed_changes(ccr)) else (
            "candidate-version (authored)" if initrel else "NONE"
        )
        print("=" * 72)
        print(f"CCR         : {ccr_id}")
        print(f"Title       : {ccr.title}")
        print(f"change_set  : {'present' if ccr.change_set else 'None (authored path)'}")
        print(f"Body source : {source}")
        print(f"Judge input : {len(proposed):,} chars   bodies present: {has_bodies}")
        print("=" * 72)
        print("---- first 900 chars of what the judge will score ----")
        print(proposed[:900])
        print("..." if len(proposed) > 900 else "")

        if not has_bodies:
            print(
                "\n[TIER 1 FAIL] No content bodies in the judge input for this CCR. "
                "If this is a description-only CCR that's expected; pick an "
                "AI-generated one via CCR_ID."
            )
            sys.exit(3)
        print("\n[TIER 1 PASS] Generated content bodies ARE in the judge input.")

        if not live:
            print("\nRe-run with --live (and ANTHROPIC_API_KEY) for the real scoring run.")
            await engine.dispose()
            return

        # TIER 2 — real Anthropic call, direct to the judge (no QAReview persisted).
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("\n[TIER 2 SKIP] set ANTHROPIC_API_KEY to run the live judge.")
            await engine.dispose()
            return
        from app.ai.client import AIClient  # lazy: only when actually going live

        print("\n---- calling the real AI judge (one Anthropic call) ----")
        judgement = await AIClient(api_key=api_key).judge(summary, proposed)
        for j in judgement.judgements:
            print(f"\n[{j.dimension}]  score={j.score}/5")
            print(f"  evidence: {j.evidence}")
        print(
            "\n[TIER 2] Read the evidence above: it should quote/paraphrase the "
            "actual lesson content, not say 'underspecified/thin'."
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
