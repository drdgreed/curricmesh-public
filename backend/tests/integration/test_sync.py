"""Integration tests for the external-sync API (V3-C).

Covers the simulated GitHub sync happy path (200 + a ``sync_logs`` row with a
url), GET sync-log, the unknown-target 400, the failure contract (a provider
that raises records a ``failed`` log AND surfaces a 502 — never a silent
success), RBAC, and cross-tenant isolation (org A cannot read org B's sync-log).

The simulated providers make ZERO network calls — they are pure functions of the
manifest, asserted by construction (no httpx/transport is touched by them). The
isolation proof runs under the test DB superuser (RLS is bypassed — P-001), so
the 404/empty results prove the APPLICATION-layer auto-filter, exactly as in
test_tenant_api_isolation.py.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_access_token
from app.database import get_db
from app.main import app
from app.models.curriculum import Curriculum
from app.models.enums import LifecycleStatus
from app.models.structure import Module
from app.models.sync import SyncLog
from app.models.version import Version
from app.routers.sync import resolve_sync_provider
from app.sync.base import SyncResult, VersionManifest
from app.tenant import use_org
from tests.conftest import DEFAULT_ORG_ID


# ---------------------------------------------------------------------------
# Transport + token helpers (mirror test_pins.py)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_transport(session: AsyncSession):
    async def _override_get_db():
        yield session

    # Snapshot existing overrides so we restore (not wipe) on exit — the caller
    # may have installed its own override (e.g. resolve_sync_provider) before
    # entering this context.
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


def _auth(role: str = "architect", org: uuid.UUID = DEFAULT_ORG_ID) -> dict:
    token = create_access_token(sub=str(uuid.uuid4()), role=role, org=org)
    return {"Authorization": f"Bearer {token}"}


async def _seed_curriculum_and_version(
    session: AsyncSession, *, with_modules: bool = True
) -> tuple[uuid.UUID, uuid.UUID]:
    cur = Curriculum(name="Sync Curriculum", slug=f"sync-{uuid.uuid4().hex[:6]}")
    session.add(cur)
    await session.flush()
    v = Version(curriculum_id=cur.id, major=2, minor=3, patch=1)
    session.add(v)
    await session.flush()
    if with_modules:
        session.add_all(
            [
                Module(version_id=v.id, index=1, focus="Intro"),
                Module(version_id=v.id, index=2, focus="Advanced"),
            ]
        )
    # Make this the active version so resolution is deterministic.
    cur.current_version_id = v.id
    await session.commit()
    return cur.id, v.id


# ---------------------------------------------------------------------------
# Happy path: simulated github sync → success + log + GET
# ---------------------------------------------------------------------------


async def test_sync_github_success_and_log(db_session: AsyncSession):
    """Simulated github sync returns success with a url and persists a log row."""
    cur_id, v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/curricula/{cur_id}/sync",
            params={"target": "github"},
            headers=_auth("architect"),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["target"] == "github"
    assert body["status"] == "success"
    assert body["detail"]["url"].startswith("https://github.com/")
    assert body["version_id"] == str(v_id)

    # A sync_logs row was persisted with status success + a url, in DEFAULT_ORG.
    row = (
        await db_session.execute(
            select(SyncLog).where(SyncLog.id == uuid.UUID(body["id"]))
        )
    ).scalar_one()
    assert row.status == "success"
    assert row.target == "github"
    assert row.detail["url"].startswith("https://github.com/")
    assert row.organization_id == DEFAULT_ORG_ID


async def test_sync_lms_success(db_session: AsyncSession):
    """Simulated lms sync returns a course url + success."""
    cur_id, _v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/curricula/{cur_id}/sync",
            params={"target": "lms"},
            headers=_auth("devops"),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["target"] == "lms"
    assert body["status"] == "success"
    assert "/courses/" in body["detail"]["url"]


async def test_get_sync_log_returns_history(db_session: AsyncSession):
    """GET sync-log returns the attempts for the curriculum, newest first."""
    cur_id, _v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        await client.post(
            f"/api/v1/curricula/{cur_id}/sync",
            params={"target": "github"},
            headers=_auth("architect"),
        )
        await client.post(
            f"/api/v1/curricula/{cur_id}/sync",
            params={"target": "lms"},
            headers=_auth("architect"),
        )
        log = await client.get(
            f"/api/v1/curricula/{cur_id}/sync-log", headers=_auth("architect")
        )
    assert log.status_code == 200
    targets = {entry["target"] for entry in log.json()}
    assert targets == {"github", "lms"}
    assert len(log.json()) == 2


# ---------------------------------------------------------------------------
# Unknown target → 400
# ---------------------------------------------------------------------------


async def test_sync_unknown_target_400(db_session: AsyncSession):
    cur_id, _v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/curricula/{cur_id}/sync",
            params={"target": "bitbucket"},
            headers=_auth("architect"),
        )
    assert resp.status_code == 400
    assert "Unknown sync target" in resp.text


# ---------------------------------------------------------------------------
# Draft-only fallback → 400 (a draft is never published)
# ---------------------------------------------------------------------------


async def test_sync_draft_only_no_active_version_400(db_session: AsyncSession):
    """A curriculum whose only version is a draft (and current_version_id is
    None) cannot be synced: the active-version fallback finds nothing → 400, and
    NO sync_logs row is written."""
    cur = Curriculum(name="Draft Only", slug=f"draft-{uuid.uuid4().hex[:6]}")
    db_session.add(cur)
    await db_session.flush()
    # Only version is a draft; current_version_id stays None.
    v = Version(
        curriculum_id=cur.id,
        major=1,
        minor=0,
        patch=0,
        status=LifecycleStatus.draft,
    )
    db_session.add(v)
    await db_session.commit()
    cur_id = cur.id

    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/curricula/{cur_id}/sync",
            params={"target": "github"},
            headers=_auth("architect"),
        )

    assert resp.status_code == 400, resp.text
    assert "no version to sync" in resp.text

    # No sync_logs row was written for this curriculum.
    rows = (
        await db_session.execute(
            select(SyncLog).where(SyncLog.curriculum_id == cur_id)
        )
    ).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


async def test_sync_rbac_forbidden(db_session: AsyncSession):
    """A non-privileged role (instructor) cannot sync → 403."""
    cur_id, _v_id = await _seed_curriculum_and_version(db_session)
    async with _make_transport(db_session) as client:
        resp = await client.post(
            f"/api/v1/curricula/{cur_id}/sync",
            params={"target": "github"},
            headers=_auth("instructor"),
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Failure contract: a raising provider → failed log AND surfaced error (502)
# ---------------------------------------------------------------------------


class _FailingProvider:
    """A provider that always raises — injected to prove the failure path."""

    async def publish(self, manifest: VersionManifest) -> SyncResult:
        raise RuntimeError("simulated upstream outage")


async def test_sync_failure_records_failed_log_and_surfaces_error(
    db_session: AsyncSession,
):
    """A failing provider records status=failed AND returns 502 — not a silent 200."""
    cur_id, _v_id = await _seed_curriculum_and_version(db_session)
    app.dependency_overrides[resolve_sync_provider] = lambda: _FailingProvider()
    try:
        async with _make_transport(db_session) as client:
            resp = await client.post(
                f"/api/v1/curricula/{cur_id}/sync",
                params={"target": "github"},
                headers=_auth("architect"),
            )
    finally:
        app.dependency_overrides.pop(resolve_sync_provider, None)

    # Error surfaced — never a silent success.
    assert resp.status_code == 502
    assert "simulated upstream outage" in resp.text

    # AND a failed attempt was logged.
    rows = (
        await db_session.execute(
            select(SyncLog).where(SyncLog.curriculum_id == cur_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].detail["url"] is None
    assert "simulated upstream outage" in rows[0].detail["message"]


# ---------------------------------------------------------------------------
# Zero network: the simulated providers are pure (no httpx involved)
# ---------------------------------------------------------------------------


async def test_simulated_providers_make_zero_network_calls():
    """Construct + call both providers directly — they touch no transport.

    Asserts by construction: the providers depend only on the manifest and
    config, returning a deterministic fake url with no HTTP client in scope.
    """
    from app.sync.providers import GitHubSyncProvider, LmsSyncProvider

    manifest = VersionManifest(
        curriculum_id=uuid.uuid4(),
        curriculum_name="Pure",
        version="v1.0.0",
        modules=["A", "B"],
        released_at="2026-06-05T00:00:00+00:00",
    )
    gh = await GitHubSyncProvider().publish(manifest)
    lms = await LmsSyncProvider().publish(manifest)
    assert gh.status == "success" and gh.url.startswith("https://github.com/")
    assert lms.status == "success" and "/courses/" in lms.url


# ---------------------------------------------------------------------------
# Tenant isolation: org A cannot read org B's sync-log
# ---------------------------------------------------------------------------


async def test_sync_tenant_isolation(db_session: AsyncSession):
    """An org-A token cannot read an org-B curriculum's sync-log.

    Org B's curriculum is invisible to org A's tenant filter, so the existence
    check 404s (consistent with POST and the rest of the codebase) — not a
    leak-revealing ``200 []``. Org B still sees its own log entry.
    """
    from sqlalchemy import text

    other_org = uuid.uuid4()
    # Seed a curriculum + version + sync log entirely inside the OTHER org.
    await db_session.execute(
        text("INSERT INTO organizations (id, name) VALUES (:id, :n)"),
        {"id": str(other_org), "n": "Other Org"},
    )
    with use_org(other_org):
        cur = Curriculum(name="B Curriculum", slug=f"b-{uuid.uuid4().hex[:6]}")
        db_session.add(cur)
        await db_session.flush()
        v = Version(curriculum_id=cur.id, major=1, minor=0, patch=0)
        db_session.add(v)
        await db_session.flush()
        log = SyncLog(
            curriculum_id=cur.id,
            version_id=v.id,
            target="github",
            status="success",
            detail={"url": "https://github.com/x/y/releases/tag/v1.0.0", "message": "ok"},
        )
        db_session.add(log)
        await db_session.commit()
        other_cur_id = cur.id
        assert log.organization_id == other_org

    db_session.expunge_all()

    async with _make_transport(db_session) as client:
        # org-A view of org-B's sync-log: the curriculum is invisible to org A's
        # tenant filter, so the existence check 404s (no leak).
        a_view = await client.get(
            f"/api/v1/curricula/{other_cur_id}/sync-log",
            headers=_auth(org=DEFAULT_ORG_ID),
        )
        # The other org still sees its own log entry.
        b_view = await client.get(
            f"/api/v1/curricula/{other_cur_id}/sync-log",
            headers=_auth(org=other_org),
        )

    assert a_view.status_code == 404
    assert b_view.status_code == 200
    assert len(b_view.json()) == 1
    assert b_view.json()[0]["target"] == "github"
