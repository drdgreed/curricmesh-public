"""S2 — deck orchestrator + endpoint (author-time router).

Drives ``app.builder.deck_generator.generate_deck_for_course`` and the
``POST /courses/{id}/generate-deck`` handler against a freshly-created,
RLS-enabled schema, injecting a FAKE ``DeckGenerator`` — ZERO real Anthropic
calls in CI. Harness mirrors tests/authoring_ai/test_authoring_ai_api.py.

Asserted:
  * grounding — the course's objectives + item bodies reach the seam, ordered;
  * structure — the RETURNED deck.md carries Marp front-matter, the theme block,
    `---` slide breaks, `<!-- ACT` markers, a concept slide, and an anti-pattern
    (proves the orchestrator/endpoint pass the artifact through unmodified);
  * empty course degrades gracefully (seam called with empty lists, still a deck);
  * unknown / cross-org course id -> None (orchestrator) / 404 (endpoint).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register every model on Base.metadata)
from app.ai.schemas import DeckDiagramSpec, GeneratedDeck
from app.slides.from_generated import diagrams_from_specs
from app.builder.deck_generator import generate_deck_for_course
from app.builder.router_course import align_item, create_course, create_item, create_objective
from app.builder.schemas import AlignmentCreate, CourseCreate, ItemCreate, ObjectiveCreate
from app.config import settings
from app.database import Base
from app.db.rls import apply_rls
from app.models.enums import AssetKind
from app.models.org import Organization
from app.models.user import User
from app.routers.authoring_ai import generate_deck_endpoint
from app.tenant import current_org, use_org

_ENUM_TYPES = ("lifecyclestatus", "assetkind")


# A realistic, standard-shaped deck the fake returns — lets the tests assert the
# orchestrator/endpoint pass the artifact through with its STRUCTURE intact.
_SAMPLE_DECK = """---
marp: true
theme: career-forge
paginate: true
header: "AI Engineering 101"
footer: "career-forge.org | v1.0.0"
size: 16:9
style: |
  section { background: #F7F6F2; }
  section .callout-anti { border-left: 4px solid #A13544; }
module_id: "sample"
deck_version: "1.0.0"
---

<!-- ACT 1: HOOK & ANCHOR -->

<!-- _class: title -->
# Sample Module

---

## Concept 1 — The Core Idea

🟦 **Concept:** the mental model this slide installs.

![A left-to-right flow from Perceive to Act.](../diagrams/agent_loop.png)

---

<!-- ACT 3: CONCEPT BUILD-UP -->

## Concept 2 — What To Avoid

🟥 **Anti-pattern:** do not wrap deterministic logic in an agent loop.
"""


@pytest.fixture
async def rls_engine():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for enum_name in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(apply_rls)
    yield engine
    await engine.dispose()


async def _two_orgs_and_user(engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        org_a = Organization(name="Org A")
        org_b = Organization(name="Org B")
        s.add_all([org_a, org_b])
        await s.flush()
        user = User(email="deck@example.com", role="instructor", organization_id=org_a.id)
        s.add(user)
        await s.commit()
        return org_a.id, org_b.id, user.id


async def _open_org_session(engine, org_id: uuid.UUID) -> AsyncSession:
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(org_id)},
    )
    return session


class FakeDeckGenerator:
    """Records the grounding it was handed; returns a realistic standard-shaped deck."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_deck(
        self, *, module_title: str, module_number: str, module_id: str,
        objectives: list[dict], items: list[dict], bloom_ceiling: str | None = None,
        language: str = "en",
    ) -> GeneratedDeck:
        self.calls.append({
            "module_title": module_title,
            "module_number": module_number,
            "module_id": module_id,
            "objectives": objectives,
            "items": items,
            "bloom_ceiling": bloom_ceiling,
            "language": language,
        })
        return GeneratedDeck(
            deck_markdown=_SAMPLE_DECK,
            diagram_specs=[
                DeckDiagramSpec(
                    filename="agent_loop",
                    mermaid="flowchart LR\n  A[Perceive] --> B[Act]",
                    alt_text="A left-to-right flow from Perceive to Act.",
                )
            ],
            summary="A 3-act sample deck for the module.",
            caveats=["Visual fit is unverified until rendered via S1."],
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def test_generate_deck_grounds_objectives_and_items_and_returns_structured_deck(rls_engine):
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Agent Loop Mastery", description="Loops"),
                    current=current, db=session,
                )
                obj = await create_objective(
                    course.id,
                    ObjectiveCreate(text="Implement a ReAct loop", week_index=1),
                    current=current, db=session,
                )
                item = await create_item(
                    course.id,
                    ItemCreate(
                        title="Loop Walkthrough",
                        kind=AssetKind.lesson_plan,
                        content="The messages list IS working memory.",
                        week_index=1,
                    ),
                    current=current, db=session,
                )
                await align_item(
                    item.id, AlignmentCreate(objective_id=obj.id),
                    current=current, db=session,
                )

                fake = FakeDeckGenerator()
                deck = await generate_deck_for_course(session, course.id, fake)

                assert deck is not None
                # --- grounding: the course's objective + item body reached the seam ---
                call = fake.calls[0]
                assert call["module_title"] == "Agent Loop Mastery"
                assert call["module_id"] == "agent-loop-mastery"  # slugified
                assert any("Implement a ReAct loop" == o["text"] for o in call["objectives"])
                assert any(
                    it["content"] == "The messages list IS working memory."
                    and it["kind"] == "lesson_plan"
                    for it in call["items"]
                )
                # --- structure of the returned deck.md (passed through intact) ---
                md = deck.deck_markdown
                assert "marp: true" in md and "theme: career-forge" in md
                assert "style:" in md  # the theme block
                assert md.count("---") >= 3  # front-matter close + slide breaks
                assert "<!-- ACT" in md
                assert "🟦" in md  # at least one concept slide
                assert "🟥" in md  # at least one anti-pattern
                # The structural diagram is a PNG image ref, NOT an inline mermaid
                # block — Marp renders inline ```mermaid``` as raw code, not a diagram.
                assert "](../diagrams/agent_loop.png)" in md
                assert "```mermaid" not in md
                # diagram_specs carry the Mermaid source keyed to the SAME stem the
                # deck's image ref points at, so render_deck can produce the PNG.
                assert deck.diagram_specs
                spec = deck.diagram_specs[0]
                assert spec.filename == "agent_loop"
                assert spec.mermaid.strip()  # real Mermaid source is present
                assert diagrams_from_specs(deck.diagram_specs) == {"agent_loop": spec.mermaid}
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_deck_empty_course_degrades_gracefully(rls_engine):
    """A course with no objectives/items still generates — the seam is called with
    empty lists (it degrades to a skeleton + caveats), never crashing."""
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Bare Course"), current=current, db=session,
                )
                fake = FakeDeckGenerator()
                deck = await generate_deck_for_course(session, course.id, fake)
                assert deck is not None
                call = fake.calls[0]
                assert call["objectives"] == []
                assert call["items"] == []
                assert call["module_title"] == "Bare Course"
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_deck_unknown_course_returns_none(rls_engine):
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                fake = FakeDeckGenerator()
                deck = await generate_deck_for_course(session, uuid.uuid4(), fake)
                assert deck is None
                assert fake.calls == []  # never called the model for a missing course
            finally:
                await session.close()
    finally:
        current_org.reset(token)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


async def test_generate_deck_endpoint_returns_deck(rls_engine):
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Course"), current=current, db=session,
                )
                deck = await generate_deck_endpoint(
                    course.id, current=current, db=session, author_ai=FakeDeckGenerator(),
                )
                assert "marp: true" in deck.deck_markdown
                assert deck.summary
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_deck_endpoint_unknown_course_404(rls_engine):
    engine = rls_engine
    org_a, _org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                with pytest.raises(HTTPException) as exc:
                    await generate_deck_endpoint(
                        uuid.uuid4(), current=current, db=session,
                        author_ai=FakeDeckGenerator(),
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)


async def test_generate_deck_endpoint_cross_org_404(rls_engine):
    """A course created under org A is invisible (404) to a session pinned to org B."""
    engine = rls_engine
    org_a, org_b, user_id = await _two_orgs_and_user(engine)
    current: dict[str, Any] = {"sub": str(user_id), "role": "instructor"}

    token = current_org.set(org_a)
    try:
        with use_org(org_a):
            session = await _open_org_session(engine, org_a)
            try:
                course = await create_course(
                    CourseCreate(title="Org A Course"), current=current, db=session,
                )
                course_id = course.id
            finally:
                await session.close()
    finally:
        current_org.reset(token)

    token = current_org.set(org_b)
    try:
        with use_org(org_b):
            session = await _open_org_session(engine, org_b)
            try:
                with pytest.raises(HTTPException) as exc:
                    await generate_deck_endpoint(
                        course_id, current=current, db=session,
                        author_ai=FakeDeckGenerator(),
                    )
                assert exc.value.status_code == 404
            finally:
                await session.close()
    finally:
        current_org.reset(token)
