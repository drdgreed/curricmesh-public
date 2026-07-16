"""End-to-end cross-tenant API isolation (MT6).

Where ``test_app_scoping.py`` exercises the ``do_orm_execute`` auto-filter
directly on a session, this module proves it through the **running API**: a real
HTTP request flows router dep → ``tenant_context`` (binds the JWT ``org`` claim
to the ``current_org`` ContextVar) → ``get_db`` → the endpoint's
``select(...).where(id == ...)`` → the auto-filter scopes it to the token's org
→ another tenant's row is invisible → the router's ``scalar_one_or_none()``
returns ``None`` → **404** (never 403, so existence isn't leaked).

The whole proof runs under the test DB superuser (RLS is bypassed — P-001), so
any isolation observed is the APPLICATION layer doing the work, exactly as it
will when the production role also can't be trusted to be least-privilege.

Transport wiring mirrors ``test_api_curricula.py``: ``get_db`` is overridden to
yield the shared fixture session, and ``create_access_token(..., org=...)`` mints
the request's tenant scope.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app
from app.models.curriculum import Curriculum
from app.tenant import get_current_org, use_org
from tests.conftest import DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Transport + token helpers (mirror test_api_curricula.py)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_transport(session: AsyncSession):
    import httpx

    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth(org: uuid.UUID, role: str = "architect") -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=org)
    return {"Authorization": f"Bearer {token}"}


def _pin_default_org(session):
    return session.execute(
        text("SELECT set_config('app.current_org', :org, false)"),
        {"org": str(DEFAULT_ORG_ID)},
    )


async def _make_other_org(session) -> uuid.UUID:
    """Insert a second organization (organizations is not RLS-scoped)."""
    other = uuid.uuid4()
    await session.execute(
        text("INSERT INTO organizations (id, name) VALUES (:id, :n)"),
        {"id": str(other), "n": "Other Org"},
    )
    return other


async def _seed_two_org_curricula(db_session) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Seed one curriculum in DEFAULT_ORG and one in a freshly-created other org.

    Returns ``(other_org_id, default_curriculum_id, other_curriculum_id)``. Both
    rows are flushed (visible to the same session/transaction the API will read
    through) and the identity map is cleared so the router's selects hit the DB
    and pass through the auto-filter.
    """
    other_org = await _make_other_org(db_session)

    mine = Curriculum(name="Default Curriculum", slug=f"def-{uuid.uuid4().hex[:8]}")
    db_session.add(mine)
    await db_session.flush()
    mine_id = mine.id

    with use_org(other_org):
        theirs = Curriculum(name="Other Curriculum", slug=f"oth-{uuid.uuid4().hex[:8]}")
        db_session.add(theirs)
        await db_session.flush()
        theirs_id = theirs.id
        assert theirs.organization_id == other_org

    db_session.expunge_all()
    return other_org, mine_id, theirs_id


# ---------------------------------------------------------------------------
# B. Cross-tenant GET → 404 (not 403 — don't leak existence)
# ---------------------------------------------------------------------------


async def test_default_org_token_cannot_get_other_org_curriculum(db_session):
    """A DEFAULT_ORG JWT GETs its own curriculum (200) but the other org's → 404.

    This is the core MT6 proof: the token's ``org`` claim scopes the read, and a
    cross-tenant id resolves to "not found", not "forbidden".
    """
    _other_org, mine_id, theirs_id = await _seed_two_org_curricula(db_session)

    async with _make_transport(db_session) as client:
        # Own-tenant row is reachable.
        ok = await client.get(
            f"/api/v1/curricula/{mine_id}", headers=_auth(DEFAULT_ORG_ID)
        )
        # Other-tenant row is hidden — 404, never 403 (no existence leak).
        hidden = await client.get(
            f"/api/v1/curricula/{theirs_id}", headers=_auth(DEFAULT_ORG_ID)
        )

    assert ok.status_code == 200
    assert ok.json()["id"] == str(mine_id)
    assert hidden.status_code == 404, "cross-tenant GET must 404, not leak/serve the row"


async def test_dashboard_excludes_other_org_curricula(db_session):
    """The DEFAULT_ORG dashboard lists only DEFAULT_ORG curricula.

    A second access path (the rollup endpoint) confirms list-style reads are
    scoped too — the other org's curriculum is absent from the payload.
    """
    _other_org, mine_id, theirs_id = await _seed_two_org_curricula(db_session)

    async with _make_transport(db_session) as client:
        resp = await client.get("/api/v1/dashboard", headers=_auth(DEFAULT_ORG_ID))

    assert resp.status_code == 200
    listed = {c["id"] for c in resp.json()["curricula"]}
    assert str(mine_id) in listed
    assert str(theirs_id) not in listed, "dashboard must not surface another tenant's curriculum"


async def test_other_org_token_sees_inverse(db_session):
    """An other-org JWT sees the other-org curriculum (200) and NOT DEFAULT_ORG's (404).

    Proves it is the TOKEN's ``org`` claim that scopes the request — not a global
    default. ``tenant_context`` binds the claim to the ContextVar, and the
    app-layer auto-filter reads that ContextVar, so the same fixture session
    (pinned-GUC notwithstanding — the superuser bypasses RLS, so only the
    app-filter is load-bearing here) returns the *opposite* visibility.
    """
    other_org, mine_id, theirs_id = await _seed_two_org_curricula(db_session)

    async with _make_transport(db_session) as client:
        ours = await client.get(
            f"/api/v1/curricula/{theirs_id}", headers=_auth(other_org)
        )
        default_hidden = await client.get(
            f"/api/v1/curricula/{mine_id}", headers=_auth(other_org)
        )

    assert ours.status_code == 200, (
        "other-org token must see the other-org curriculum — the token's org "
        "scopes the read, not a fixed default"
    )
    assert ours.json()["id"] == str(theirs_id)
    assert default_hidden.status_code == 404, "other-org token must NOT see DEFAULT_ORG's row"


# ---------------------------------------------------------------------------
# C. Context reset / no-leak across sequential requests
# ---------------------------------------------------------------------------


async def test_tenant_context_resets_after_request(db_session):
    """After a request completes, ``get_current_org()`` is back to the fixture's
    DEFAULT_ORG — the async ``tenant_context`` yield-dependency reset cleanly.

    Guards the ContextVar set/reset symmetry (P-003): the request set the
    ContextVar to its token's org, then ``finally: current_org.reset(token)``
    restored the prior (fixture-pinned DEFAULT_ORG) value — no bleed into the
    surrounding context.
    """
    _other_org, mine_id, _theirs_id = await _seed_two_org_curricula(db_session)

    # Fixture pins DEFAULT_ORG before the request.
    assert get_current_org() == DEFAULT_ORG_ID

    async with _make_transport(db_session) as client:
        r1 = await client.get(
            f"/api/v1/curricula/{mine_id}", headers=_auth(DEFAULT_ORG_ID)
        )
        # After request 1 the ContextVar must be reset to its prior value.
        assert get_current_org() == DEFAULT_ORG_ID, "context leaked after request 1"

        # A second sequential request on the same app must also reset cleanly,
        # even when scoped to a DIFFERENT org — no bleed from request to request.
        r2 = await client.get("/api/v1/curricula", headers=_auth(_other_org))
        assert get_current_org() == DEFAULT_ORG_ID, "context leaked after request 2"

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Final guard: the surrounding (fixture) context is untouched.
    assert get_current_org() == DEFAULT_ORG_ID
