"""Phase 1 hardening (slice 6) — the generation cost cap + generation telemetry.

Two verifications of the slice-4/5 hardening the acceptance slice signs off on:

1. **Cost cap** — ``POST /generate-course`` bounds fan-out via ``objectives_count``
   (<= 20, the ``1 + 2*count`` worst-case governor). ``objectives_count > 20`` is a
   422 at the schema boundary, before any generation runs.

2. **Generation telemetry** — the author-time ``CourseAuthorAI`` generators
   (``generate_objectives`` / ``generate_item_content`` / ``generate_assessment``,
   which ``generate_course`` composes) are governed calls: each routes through
   ``AIClient._parse`` -> ``_invoke``, which records a durable per-call usage row
   via ``usage_store.record_event`` keyed on the structured-output type name.
   Telemetry is therefore ALREADY wired for generation — no new code needed. We
   prove it by driving the REAL ``AIClient`` generators against a FAKE Anthropic
   transport (canned parsed output + usage) and asserting ``record_event`` fires
   with the right ``feature`` — mirroring ``tests/ai/test_usage_persistence.py``.
   Zero real Anthropic calls in CI.
"""

from __future__ import annotations

import types
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import usage_store
from app.ai.client import AIClient
from app.ai.schemas import (
    GeneratedAssessment,
    GeneratedItemContent,
    GeneratedObjective,
    GeneratedObjectives,
)
from app.auth.jwt import create_access_token
from app.builder.models import DraftItem, DraftObjective
from app.database import get_db
from app.main import app
from app.routers.authoring_ai import get_author_ai, get_generation_session_scope
from tests.builder.test_course_generator import FakeAuthorAI
from tests.conftest import DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# 1. Cost cap — objectives_count > 20 is rejected before any generation runs.
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _transport(session: AsyncSession, author_ai):
    async def _override_get_db():
        yield session

    @asynccontextmanager
    async def _yield_test_session(_org_id):
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_author_ai] = lambda: author_ai
    app.dependency_overrides[get_generation_session_scope] = lambda: _yield_test_session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(role: str) -> dict:
    token = create_access_token(
        sub=str(uuid.uuid4()), role=role, org=DEFAULT_ORG_ID
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_generation_cost_cap_rejects_oversized_brief(db_session: AsyncSession):
    """The cost governor: objectives_count > 20 -> 422, no generation attempted.

    The fake generator counts its calls; a 422 at the boundary means NONE ran —
    the cap prevents an unbounded, expensive fan-out before it can start.
    """
    fake = FakeAuthorAI(count=21)
    async with _transport(db_session, fake) as client:
        resp = await client.post(
            "/api/v1/builder/generate-course",
            json={
                "title": "Too Big",
                "topic": "Everything",
                "learner_profile": {},
                "target_weeks": 8,
                "objectives_count": 21,  # over the cap
            },
            headers=_auth("architect"),
        )
    assert resp.status_code == 422, resp.text
    # The cap is enforced at the schema boundary — no generator call was made.
    assert fake.calls == {"objectives": 0, "content": 0, "assessment": 0}


@pytest.mark.asyncio
async def test_generation_cost_cap_allows_the_boundary(db_session: AsyncSession):
    """objectives_count == 20 is allowed (inclusive bound) and fully generates."""
    fake = FakeAuthorAI(count=20)
    async with _transport(db_session, fake) as client:
        headers = _auth("architect")
        resp = await client.post(
            "/api/v1/builder/generate-course",
            json={
                "title": "At The Cap",
                "topic": "Bounded",
                "learner_profile": {},
                "target_weeks": 10,
                "objectives_count": 20,
            },
            headers=headers,
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]

        poll = await client.get(
            f"/api/v1/builder/generate-course/jobs/{job_id}", headers=headers
        )
        assert poll.status_code == 200, poll.text
        job = poll.json()
        assert job["status"] == "complete", job
        assert job["total_steps"] == 1 + 2 * 20  # the cost governor's worst case
        course_id = uuid.UUID(job["course_id"])

    # 1 + 2*20 generations, all landed: 20 objectives + 40 items in the DB.
    n_obj = (
        await db_session.execute(
            select(func.count())
            .select_from(DraftObjective)
            .where(DraftObjective.draft_course_id == course_id)
        )
    ).scalar_one()
    n_items = (
        await db_session.execute(
            select(func.count())
            .select_from(DraftItem)
            .where(DraftItem.draft_course_id == course_id)
        )
    ).scalar_one()
    assert n_obj == 20
    assert n_items == 40


# ---------------------------------------------------------------------------
# 2. Generation telemetry — each generator records a governed usage event.
#    (Case A: ALREADY WIRED via AIClient._parse; we assert it fires.)
# ---------------------------------------------------------------------------


def _fake_anthropic_client(parsed):
    """A fake anthropic client whose messages.parse returns canned output + usage."""

    async def _parse(**kwargs):
        usage = types.SimpleNamespace(input_tokens=13, output_tokens=27)
        return types.SimpleNamespace(
            parsed_output=parsed, usage=usage, stop_reason="end_turn"
        )

    return types.SimpleNamespace(messages=types.SimpleNamespace(parse=_parse))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name,kwargs,parsed,expected_feature",
    [
        (
            "generate_objectives",
            {"topic": "agents", "learner_profile": {}, "count": 3},
            GeneratedObjectives(
                objectives=[
                    GeneratedObjective(
                        text="Build a tool-using agent", bloom_level="create"
                    )
                ]
            ),
            "GeneratedObjectives",
        ),
        (
            "generate_item_content",
            {"objective": "o", "kind": "lesson_plan", "course_context": "c"},
            GeneratedItemContent(
                kind="lesson_plan", content_markdown="# Body", summary="s"
            ),
            "GeneratedItemContent",
        ),
        (
            "generate_assessment",
            {"objective": "o", "course_context": "c"},
            GeneratedAssessment(content_markdown="## Quiz", rubric="r"),
            "GeneratedAssessment",
        ),
    ],
)
async def test_generator_records_usage_telemetry(
    monkeypatch, method_name, kwargs, parsed, expected_feature
):
    """Each author-time generator records a governed usage event (feature = type name).

    Proves generation telemetry is ALREADY wired: the generators route through
    AIClient._parse -> _invoke -> usage_store.record_event. We monkeypatch
    record_event (like tests/ai/test_usage_persistence.py) so the assertion is
    deterministic and never touches the DB.
    """
    captured: list[dict] = []

    def _fake_record_event(**kw):
        captured.append(kw)

    monkeypatch.setattr(usage_store, "record_event", _fake_record_event)

    client = AIClient(api_key="x")
    client._client = _fake_anthropic_client(parsed)

    result = await getattr(client, method_name)(**kwargs)
    assert result is parsed  # the generator returned the (canned) structured output

    assert len(captured) == 1, "exactly one usage event per generation call"
    event = captured[0]
    assert event["feature"] == expected_feature
    assert event["model"] == client.model
    assert event["input_tokens"] == 13
    assert event["output_tokens"] == 27
    assert event["stop_reason"] == "end_turn"
    assert isinstance(event["latency_ms"], int)
